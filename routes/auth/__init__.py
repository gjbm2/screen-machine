"""Accounts (owner, 19 Nov): Google sign-in, roles and per-person generator spaces. See docs/ACCOUNTS.md.

All of it is deployment config: the `accounts` capability switches it on, and deployments/<id>/private/accounts.yaml
(gitignored) holds the Google OAuth client ID and who is admin or member. Without a client ID every caller is treated
as an admin, so the app behaves as before, while new generations are already stamped with the space that made them.

- Who: a signed session cookie (sm_session) names a Google account; the private accounts file gives it a role. Anyone else is a guest: an unlisted Google account keeps its own space on every device, an
  anonymous browser gets one from the sm_space cookie. Processes on this machine (127.0.0.1) pass every gate.
- What: config/auth/gates.yaml says which API calls need which role (gates.py); a destination's role is its
  `access` in publish-destinations.json (default admin).
- Spaces: each generator batch is noted against its space (spaces.py); the Generated tab and the jobs feed show a
  viewer only their own (an admin may ask for everyone's).
"""
from routes.auth.core import (  # noqa: F401
    ROLES, Viewer, active, client_id, destination_access, can_use_destination, viewer, own_space, can_see_space,
    can_see_job, job_visibility, after_request,
)
from routes.auth.gates import gate  # noqa: F401
from routes.auth.spaces import note_batch, batch_space  # noqa: F401
