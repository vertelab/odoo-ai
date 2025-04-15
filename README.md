# odoo-ai

When installing the Python dependencies/requirements, one can run into issues with the pre-installed Python packages on Ubuntu 24.04, such as the one below.
```
ERROR: Cannot uninstall typing_extensions 4.10.0, RECORD file not found. Hint: The package was installed by debian.
```
To fix this issue, the easiest way is to remove the following packages.
```
sudo apt remove python3-typing-extensions python3-jsonschema
```
If you are having problems, try running the command below to make sure you don't have any broken apt packages.
```
sudo apt --fix-broken install
```
