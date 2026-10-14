"""All focused tests import the application selected by deployment.json."""
from pathlib import Path
import json,sys
ROOT=Path(__file__).resolve().parents[2]
CONFIG=json.loads((ROOT/'final_testing/deployment.json').read_bytes())
APPLICATION=ROOT/CONFIG['application_online']
sys.path.insert(0,str(APPLICATION))
# Credentials are an offline-only module; shared finance/source modules come
# from the selected online app, and missing offline-only modules from its pair.
import tempdesk
tempdesk.__path__.append(str(ROOT/CONFIG['application_offline']/'tempdesk'))
