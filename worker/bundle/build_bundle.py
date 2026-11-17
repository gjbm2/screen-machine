"""Make a private model bundle on Hugging Face from a manifest, copying files server-side
(no download through this machine). Usage: python build_bundle.py sm-image.json
Token: ~/.config/screen-machine/hf.token (never printed). Needs huggingface_hub >= 2."""
import json
import os
import sys

from huggingface_hub import CommitOperationCopy, CommitOperationDelete, HfApi

man = json.load(open(sys.argv[1]))
token = open(os.path.expanduser("~/.config/screen-machine/hf.token")).read().strip()
api = HfApi(token=token)
repo = man["repo"]
api.create_repo(repo, private=True, exist_ok=True)

want = {}
for f in man["files"]:
    dest = f.get("dest", f["path"])
    want[dest] = f
have = set(api.list_repo_files(repo))
ops = []
for dest, f in want.items():
    if dest in have:
        continue
    ops.append(CommitOperationCopy(src_path_in_repo=f["path"], path_in_repo=dest,
                                   src_repo_id=f["src"], src_repo_type="model",
                                   src_revision=f.get("rev")))
for p in sorted(have - set(want) - {".gitattributes", "README.md"}):
    ops.append(CommitOperationDelete(path_in_repo=p))
if not ops:
    print("bundle already matches the manifest")
else:
    c = api.create_commit(repo, ops, commit_message=f"bundle from {os.path.basename(sys.argv[1])}")
    print("commit", c.oid)
info = api.model_info(repo, files_metadata=True)
total = sum((s.size or 0) for s in info.siblings)
print(f"{repo} @ {info.sha}: {len(info.siblings)} files, {total / 1e9:.2f} GB")
