"""Bounded GET retry for standalone NOAA collection processes only."""
import time
from urllib.parse import urlparse

def install():
    import requests
    if getattr(requests.get,'_paper_weather_retry',False):return
    original=requests.get
    allowed={'noaa-hrrr-bdp-pds.s3.amazonaws.com','noaa-gefs-pds.s3.amazonaws.com',
             'nomads.ncep.noaa.gov','aviationweather.gov','api.weather.gov'}
    def get(url,*args,**kwargs):
        if urlparse(url).hostname not in allowed:return original(url,*args,**kwargs)
        for attempt in range(4):
            try:
                response=original(url,*args,**kwargs)
                if response.status_code not in (429,500,502,503,504) or attempt==3:return response
                response.close()
            except (requests.Timeout,requests.ConnectionError):
                if attempt==3:raise
            time.sleep(.5*2**attempt)
    get._paper_weather_retry=True;requests.get=get
