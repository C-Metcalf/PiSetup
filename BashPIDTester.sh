#!/bin/bash

sudo apt-get update -y
sudo apt-get upgrade -y
sudo apt-get autoremove -y
sudo apt install wtype
sudo apt-get install can-utils

# Put this bash script on the desktop
cd ..
mkdir code 
cd code
python -m venv venv 
cd venv/bin
source activate
cd ../..

pip install PySide6
pip install pyserial
pip install pglive

git clone https://github.com/C-Metcalf/PIDTester.git
