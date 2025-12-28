#!/bin/bash

# Get the directory where the script is located
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Activate virtual environment if it exists
if [ -d "$DIR/.venv" ]; then
    source "$DIR/.venv/bin/activate"
elif [ -d "$DIR/venv" ]; then
    source "$DIR/venv/bin/activate"
fi

# Run the python script with all passed arguments
python3 "$DIR/burn-manual.py" "$@"
