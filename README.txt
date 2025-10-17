MCC E-1608 Control GUI (PyQt6 + pyqtgraph + mcculw)

Prereqs (Windows):
  1) Install InstaCal + Universal Library (UL). Add your E-1608 in InstaCal.
  2) Create/activate a 64-bit Python venv.
  3) pip install -r requirements.txt
  4) Run: python main.py

Notes:
  - This app uses mcculw (Python wrapper) which dynamically links cbw64.dll at runtime.
  - Analog inputs are read in a software-paced loop for simplicity. This is robust and fine
    for human-in-the-loop GUIs. If you need 1 kS/s x 8 channels, we can add a hardware-
    paced a_in_scan path later.
  - Use File ▸ Load Config / Load Script to apply your JSON files.
  - Time window is adjustable 0.01–10 s. Left-click a trace: expand 3:1 and get scale dialog.
  - Digital outputs window shows eight traces aligned on the same time axis.
  - Bottom panes show Tx (commands to device) and Rx/Debug messages.

Files:
  main.py              - Application entry point and main window
  daq_driver.py        - Thin wrapper around mcculw for AI/DI/DO/AO; logs Tx/Rx
  config_manager.py    - Loads/saves both structured and legacy flat configs
  filters.py           - 1st-order low-pass filter helper
  analog_chart.py      - Stacked analog traces window (pyqtgraph)
  digital_chart.py     - Digital outputs traces window (pyqtgraph)
  script_runner.py     - Runs script.json events (Run/Pause/Reset)
