"""Every test run is a deployment's run: the tests read the shipped finsbury deployment (deployments/finsbury) unless
SM_DEPLOYMENT says otherwise. smconfig refuses to guess, so without this an unselected test run would stop at import."""
import os

os.environ.setdefault("SM_DEPLOYMENT", "finsbury")
