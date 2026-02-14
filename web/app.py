import os
import subprocess
import signal
from flask import Flask, render_template, request, redirect

app = Flask(__name__)

# --- CONFIGURATION ---
CONFIG_FILE_PATH = "/home/pi/.config/pi-radar/radar.cfg"  # Change to your actual path
RADAR_SCRIPT_PATH = "/home/pi/Pi-Radar/Radar.py"  # Path to your radar script
# ---------------------

def read_config():
    config = {}
    if not os.path.exists(CONFIG_FILE_PATH):
        return config
    with open(CONFIG_FILE_PATH, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                config[key.strip()] = val.strip()
    return config

def write_config(data):
    lines = []
    # We read the old file to preserve comments if possible, 
    # or just overwrite it cleanly:
    with open(CONFIG_FILE_PATH, 'w') as f:
        for key, value in data.items():
            f.write(f"{key}={value}\n")

@app.route('/')
def index():
    config = read_config()
    return render_template('index.html', config=config)

@app.route('/save', methods=['POST'])
def save():
    new_config = request.form.to_dict()
    
    # Format colors back to (R,G,B) from Hex if necessary
    for key in filter(lambda k: "COLOR" in k, new_config.keys()):
        hex_val = new_config[key].lstrip('#')
        rgb = tuple(int(hex_val[i:i+2], 16) for i in (0, 2, 4))
        new_config[key] = f"({rgb[0]},{rgb[1]},{rgb[2]})"
    
    write_config(new_config)
    return redirect('/')

@app.route('/restart', methods=['POST'])
def restart():
    # 1. Kill old process
    # This finds the process by filename and kills it
    subprocess.run(["pkill", "-f", os.path.basename(RADAR_SCRIPT_PATH)])
    
    # 2. Start new process with DISPLAY=:0
    env = os.environ.copy()
    env["DISPLAY"] = ":0"
    
    # Use Popen so it runs in the background
    subprocess.Popen(["python3", RADAR_SCRIPT_PATH], env=env)
    
    return "Application Restarted! <a href='/'>Go Back</a>"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
