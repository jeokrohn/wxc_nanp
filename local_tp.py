#!/usr/bin/env python
"""
In some areas in the US carriers require that called party numbers sent to the PSTN by an enterprise need to
differentiate between different destination types. For example in NPA 816 these number formats are required:

* HNPA local: 7D
* FNPA local: 10D
* HNPA toll: 1+10D
* FNPA toll: 10+10D

Here HNPA and FNPA stand for home (same NPA as caller) and foreign (different NPA than caller) NPA.

With this Python script for a given NPA/NXX the required translation patterns can be provisioned in WxC to intercept
local calls and modify the format for these calls. The idea is to then route these calls to the premises using an EDP
with a respective pattern
The information needed to determine the transformations is obtained from localcallingguide.com
"""
import argparse
import asyncio
import logging
import os.path
import re
import sys
import xml.etree.ElementTree as ET
from typing import Optional

import requests
import xmljson
import yaml
from dotenv import load_dotenv
from wxc_sdk.as_api import AsWebexSimpleApi
from wxc_sdk.integration import Integration
from wxc_sdk.locations import Location
from wxc_sdk.telephony.call_routing.translation_pattern import TranslationPattern
from wxc_sdk.tokens import Tokens

log = logging.getLogger(__name__)


def yml_path() -> str:
    """
    Get filename for YML file to cache access and refresh token
    """
    return f'local_tp.yml'


def read_tokens_from_file() -> Optional[Tokens]:
    """
    Get service app tokens from cache file, return None if cache does not exist
    """
    path = yml_path()
    if not os.path.isfile(path):
        return None
    try:
        with open(path, mode='r') as f:
            data = yaml.safe_load(f)
        tokens = Tokens.model_validate(data)
    except Exception:
        return None
    return tokens


def write_tokens_to_file(tokens: Tokens):
    """
    Write tokens to cache
    """
    with open(yml_path(), mode='w') as f:
        yaml.safe_dump(tokens.model_dump(exclude_none=True), f)


def get_access_token() -> Tokens:
    """
    Get a new access token using refresh token, service app client id, service app client secret
    """
    tokens = Tokens(refresh_token=os.getenv('SERVICE_APP_REFRESH_TOKEN'))
    integration = Integration(client_id=os.getenv('SERVICE_APP_CLIENT_ID'),
                              client_secret=os.getenv('SERVICE_APP_CLIENT_SECRET'),
                              scopes=[], redirect_url=None)
    integration.refresh(tokens=tokens)
    write_tokens_to_file(tokens)
    return tokens


def get_token() -> Optional[str]:
    """
    Get tokens from environment variable, from cache or create new access token using service app credentials
    """
    access_token = os.getenv('WEBEX_TOKEN')
    if access_token:
        return access_token
    # try to read from file
    tokens = read_tokens_from_file()
    # .. or create new access token using refresh token
    if tokens is None or tokens.remaining < 24 * 60 * 60:
        tokens = get_access_token()
    return tokens.access_token


def xmllocalprefix(npa: str, nxx: str) -> list[str]:
    """
    get list of NPA-NXXes local to given NPA-NXX from localcallingguide.com
    :param npa:
    :param nxx:
    :return:
    """
    url = "https://www.localcallingguide.com/xmllocalprefix.php"
    params = {'npa': npa, 'nxx': nxx}
    response = requests.get(url, params=params)
    data = xmljson.Parker(dict_type=dict).data(ET.fromstring(response.text))
    if 'error' in data:
        print(f'Error retrieving local NPA/NXXes: {data["error"]}', file=sys.stderr)
        exit(1)
    result = [f'{prefix["npa"]}{prefix["nxx"]}'
              for prefix in data['lca-data']['prefix']]
    return result


def single_pattern(prefix5d: str, trailing_digits: str) -> TranslationPattern:
    """
    Creates a single pattern
    :param prefix5d: first five digits of npa/nxx
    :param trailing_digits: allowed digits in last digit of npa/nxx
    :return: single translation pattern to be provisioned in WxC
    """

    # we want to convert the sequence of allowed trailing digits to (if possible) something like:
    # 1-4
    # X
    r = ''
    i = iter(trailing_digits)
    start_digit = next(i)
    done = False
    while not done:
        # get a sequence
        stop_digit = start_digit
        digit = start_digit
        try:
            while True:
                digit = next(i)
                if int(digit) - int(stop_digit) == 1:
                    stop_digit = digit
                    continue
                break
        except StopIteration:
            done = True
        if start_digit == stop_digit:
            # add a single digit
            r += start_digit
        else:
            if int(stop_digit) - int(start_digit) == 1:
                # something like "12"
                r += start_digit + stop_digit
            else:
                # something like "1-3"
                r += f'{start_digit}-{stop_digit}'
            # if .. else ..
        # if .. else ..
        start_digit = digit
    if r == '0-9':
        r = 'X'

    replacement = f'{prefix5d}$1'
    if len(r) > 1:
        pattern = f'+1{prefix5d}([{r}]'
    else:
        if r == 'X':
            pattern = f'+1{prefix5d}({r}'
        else:
            pattern = f'+1{prefix5d}{r}('
            replacement = f'{prefix5d}{r}$1'

    pattern = f'{pattern}XXXX)'

    replacement = f'90{replacement}'
    return TranslationPattern(matching_pattern=pattern, replacement_pattern=replacement, name=f'TP_{prefix5d}')


def get_patterns(npa: str, nxx: str) -> list[TranslationPattern]:
    """
    Get list of required translation patterns for given NPA/NXX
    """
    # get list of local NPA-NXXes
    npanxx = xmllocalprefix(npa=npa, nxx=nxx)
    npanxx.sort()

    # list of 5D prefixes
    prefixes = list(set((x[:5] for x in npanxx)))
    prefixes.sort()

    # required patterns
    patterns = [single_pattern(prefix5d,
                               ''.join((x[-1]
                                        for x in npanxx
                                        if x.startswith(prefix5d))))
                for prefix5d in prefixes]
    return patterns


def main():
    """
    Main code
    :return: None
    """
    logging.basicConfig(level=logging.INFO)
    args = argparse.ArgumentParser(description="""Provision translation patterns on Webex Caling for a given NPA NXX 
        to make sure that NPA/NXXes considered local are treated accordingly.
        Location level Translation patterns matching on local NPA/NXXes are provisioned in given location.""")

    args.add_argument('--npa', required=True, help='NPA of the GW location')
    args.add_argument('--nxx', required=True, help='NXX of the GW location')
    args.add_argument('--readonly', required=False, action='store_true',
                      help='Don\'t write to Webex Calling. Existing patterns are read if possible.')
    args.add_argument('--patternsonly', required=False, action='store_true',
                      help='Only print patterns required. No WxC token is required.')
    args.add_argument('--token', required=False, help='access token to access Webex Calling APIs')
    args.add_argument('--location', required=False,
                      help='Location for the location level translation patterns.')

    parsed_args = args.parse_args()

    if not parsed_args.patternsonly and not parsed_args.location:
        print('error: --location parameter is required', file=sys.stderr)
        exit(1)

    # get required translation patterns
    required_patterns = get_patterns(npa=parsed_args.npa, nxx=parsed_args.nxx)
    print(f'{len(required_patterns)} patterns are required')

    # print required patterns
    p_len = max(len(p.matching_pattern) for p in required_patterns)
    print('\n'.join(f'{p.name:9}: {p.matching_pattern:{p_len}} -> {p.replacement_pattern}'
                    for p in required_patterns))
    if len(required_patterns) > 500:
        print('Too many TPs. Can not exceed 500.', file=sys.stderr)
        exit(1)

    # if only a list of patterns is required then print the list of patterns and return
    if parsed_args.patternsonly:
        return

    # try to get an access token
    load_dotenv()
    access_token = parsed_args.token or get_token()
    if not access_token:
        print('No access token. Token can be passed as an argument, read from WEBEX_TOKEN environment variable or '
              'can be a service app token',
              file=sys.stderr)
        exit(1)

    async def wxc_provisioning():
        """
        Actually do the provisioning in Webex Calling using asyncio to allow for concurrent provisioning requests
        """
        async with AsWebexSimpleApi(tokens=access_token) as api:
            # validate location
            location = next((loc
                             for loc in await api.locations.list(name=parsed_args.location)
                             if loc.name == parsed_args.location),
                            None)
            if location is None:
                print(f'Location {parsed_args.location} not found', file=sys.stderr)
                exit(1)
            location: Location

            # get list if existing TPs
            tapi = api.telephony.call_routing.tp
            existing_tp_list = await tapi.list(limit_to_location_id=location.location_id)
            tp_re = re.compile(r'^TP_\d{5}$')
            existing_tp_list = [tp for tp in existing_tp_list
                                if tp_re.match(tp.name)]
            log.debug(f'got {len(existing_tp_list)} existing TPs')

            # check existing patterns and delete/update as required
            tasks = []
            descriptions = []
            for tp in existing_tp_list:
                required_tp = next((p for p in required_patterns
                                    if p.name == tp.name),
                                   None)
                if required_tp:
                    # a pattern for this prefix is required
                    required_tp: TranslationPattern
                    if (required_tp.matching_pattern == tp.matching_pattern and
                            required_tp.replacement_pattern == tp.replacement_pattern):
                        # no need to change this pattern
                        continue
                    # update existing TP
                    tp.matching_pattern = required_tp.matching_pattern
                    tp.replacement_pattern = required_tp.replacement_pattern
                    descriptions.append(f'update {tp.name}: {tp.matching_pattern} -> {tp.replacement_pattern}')
                    if not parsed_args.readonly:
                        tasks.append(tapi.update(pattern=tp,
                                                 location_id=location.location_id))
                else:
                    # delete existing TP
                    descriptions.append(f'delete {tp.name}')
                    if not parsed_args.readonly:
                        tasks.append(tapi.delete(translation_id=tp.id, location_id=location.location_id))

            # add new patterns
            for required_tp in required_patterns:
                existing_tp = next((p for p in existing_tp_list
                                    if p.name == required_tp.name),
                                   None)
                if existing_tp:
                    continue
                descriptions.append(f'create {required_tp.name}: '
                                    f'{required_tp.matching_pattern} -> {required_tp.replacement_pattern}')
                if not parsed_args.readonly:
                    tasks.append(tapi.create(pattern=required_tp, location_id=location.location_id))

            # display all tasks
            if not descriptions:
                print('No changes are required')
                return
            print(f'{len(descriptions)} Tasks:')
            print('\n'.join(f'  - {d}' for d in descriptions))

            # execute all tasks .. if not readonly
            if parsed_args.readonly:
                print('Readonly mode. No changes are made')
                return

            # execute all tasks
            err = False
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # see if there are any errors
            for result, description in zip(results, descriptions):
                if isinstance(result, Exception):
                    err = True
                    print(f'Error: {description}, {result}', file=sys.stderr)
            if err:
                exit(1)
        return

    asyncio.run(wxc_provisioning())
    return


if __name__ == '__main__':
    main()
