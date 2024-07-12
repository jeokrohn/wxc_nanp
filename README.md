# Automated WxC TP provisioning for local/national dialing behind PBX

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

## Webex token

To access the Webex Calling APIs a token is required. A token can be passed to the script using the `--token` option.

If no token is passed the script will try to read the token from the WEBEX_TOKEN environment variable.

In case the token is not available the script will try to use a service app access token using the parameters 
(client_id, client_secret, refresh_token) defined in the environment or `.env` file respectively. Service app tokens 
are cached in a YML file.

## Usage

    ./local_tp.py --help
    usage: local_tp.py [-h] --npa NPA --nxx NXX [--readonly] [--patternsonly]
                       [--token TOKEN] [--location LOCATION]
    
    Provision translation patterns on Webex Caling for a given NPA NXX to make sure that
    NPA/NXXes considered local are treated accordingly. Location level Translation patterns
    matching on local NPA/NXXes are provisioned in given location.
    
    options:
      -h, --help           show this help message and exit
      --npa NPA            NPA of the GW location
      --nxx NXX            NXX of the GW location
      --readonly           Don't write to Webex Calling. Existing patterns are read if
                           possible.
      --patternsonly       Only print patterns required. No WxC token is required.
      --token TOKEN        access token to access Webex Calling APIs
      --location LOCATION  Location for the location level translation patterns.
