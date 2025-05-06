# odoo-ai

When installing the Python dependencies/requirements, one can run into issues with the pre-installed Python packages on Ubuntu 24.04, such as the one below.
```
ERROR: Cannot uninstall typing_extensions 4.10.0, RECORD file not found. Hint: The package was installed by debian.
```
To fix this issue, the easiest way is to run the command below in the project/repository directory.
```
sudo pip3 install -r requirements.txt --ignore-installed --break-system-packages
```

