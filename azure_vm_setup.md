# Create Azure N-Series VM

# Install Miniconda
```
mkdir -p ~/miniconda3
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -O ~/miniconda3/miniconda.sh
bash ~/miniconda3/miniconda.sh -b -u -p ~/miniconda3
rm -rf ~/miniconda3/miniconda.sh
~/miniconda3/bin/conda init bash
~/miniconda3/bin/conda init zsh
```

# Create Virtual Env
```
# Install anaconda
conda create -n cocoa python=2.7 anaconda
# Activate environment
source activate cocoa
# Install requirements
pip install -r requirements.txt
# Repo Setup (Must be in CoCoA dir)
python setup.py develop
```
