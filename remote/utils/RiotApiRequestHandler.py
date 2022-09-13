import time
import urllib.parse
import requests as r
from typing import Dict


class RiotApiRequestHandler:

    @staticmethod
    def get(url: str, apiKey: str, params: Dict[str, str] = None, limitExceeded=False) -> r.Response:
        """
        Makes a get request for given url. This method is used to call Riot API.

        :param url: The endpoint you are making call on.
        :param apiKey: The key provided by Riot to access their API.
        :param params: Optional parameter. To include extra parameters in url put key name as dict key name and value as dict value.
        :param limitExceeded: Parameter called within function. Shouldn't be change outside this function.
        :return: Response object.
        """
        attachedParameters = ""
        if params is None:
            params = dict()
        for key, item in params.items():
            attachedParameters += f"&{key}={item}"
        encodedUrl = RiotApiRequestHandler.encodeURL(f"{url}?api_key={apiKey}{attachedParameters}")
        response = r.get(encodedUrl)

        if response.status_code == 401:
            raise r.exceptions.HTTPError("Not authorised. Check if your API key is correct and if it's not expired.")
        elif response.status_code == 429:
            if not limitExceeded:
                print("Checking if rate is exceeded.")
                time.sleep(13)
                response = RiotApiRequestHandler.get(url, apiKey, params, True)
            else:
                print("Rate limit exceeded. Waiting 2 minutes until making new request.")
                time.sleep(122)
                response = RiotApiRequestHandler.get(url, apiKey, params)

        return response

    @staticmethod
    def encodeURL(url: str) -> str:
        return urllib.parse.quote(url, safe="/:?=&")
