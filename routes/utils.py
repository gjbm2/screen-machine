import os

# Find file
def findfile(file_param):
    """
    Check if a file exists with or without a path.
    If only a filename is given, check multiple predefined directories.
    """
    # 1. Check if the provided path is already valid
    if os.path.exists(file_param):
        return file_param  # Use as-is

    # 2. Set script location
    script_dir = os.path.dirname(os.path.abspath(__file__))

    # 3. Check in predefined workflow directories
    workflow_dirs = [
        os.path.join(os.getcwd(), "src", "data"),                 # ./src/data
        os.path.join(script_dir, "data"),                         # script/data
        os.path.join(script_dir, "workflows"),                    # script/workflows
        os.path.join(script_dir, "sysprompts"),                   # script/sysprompts
        os.path.join(script_dir, "src", "workflows"),             # script/src/workflows
        os.path.join(script_dir, "src", "data", "workflows")      # script/src/data/workflows
    ]

    for directory in workflow_dirs:
        file_in_dir = os.path.join(directory, file_param)
        if os.path.exists(file_in_dir):
            return file_in_dir

    # 4. Check in current working directory
    file_in_cwd = os.path.join(os.getcwd(), file_param)
    if os.path.exists(file_in_cwd):
        return file_in_cwd

    # 5. Check in script directory directly
    file_in_script_dir = os.path.join(script_dir, file_param)
    if os.path.exists(file_in_script_dir):
        return file_in_script_dir

    # 6. Check in user's home directory
    home_dir = os.path.expanduser("~")
    file_in_home = os.path.join(home_dir, file_param)
    if os.path.exists(file_in_home):
        return file_in_home

    print(f"*** FAILED TO FIND *** {file_param}")
    return None
