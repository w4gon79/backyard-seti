# Backyard SETI

Amateur analysis of Breakthrough Listen open radio telescope data for SETI signal detection.

## Overview

This project works with the Breakthrough Listen (BL) open data archive to search for narrowband signals of potential intelligent origin. We use Berkeley's open-source tools (blimpy, turbo_seti) alongside custom analysis pipelines for:

- **Targeted star searches** - Doppler drift analysis on specific stars (Proxima Centauri, TRAPPIST-1, etc.)
- **RFI rejection** - On/off cadence comparison to reject terrestrial interference
- **Anomaly detection** - Spectral outlier identification in archived observations
- **Bulk analysis** - Systematic processing of archived data for unusual signals

## Tools

| Tool | Purpose |
|------|---------|
| [blimpy](https://github.com/UCBerkeleySETI/blimpy) | Load and visualize filterbank/HDF5 files |
| [turbo_seti](https://github.com/UCBerkeleySETI/turbo_seti) | Narrowband Doppler drift search |
| Custom scripts | RFI rejection, batch processing, visualization |

## Data Sources

- **Portal:** [Breakthrough Listen Open Data Archive](https://breakthroughinitiatives.org/opendatasearch)
- **Formats:** Filterbank (.fil) and HDF5 (.h5)
- **Telescopes:** GBT (Green Bank, WV), Parkes (Australia), FAST (China)
- **Sizes:** Typically 1-10 GB per observation file

## Project Structure

```
backyard-seti/
├── src/              # Analysis scripts
├── data/             # Downloaded BL observation files (gitignored)
├── results/          # Output plots, candidate lists (gitignored)
├── docs/             # Documentation and notes
├── README.md
└── .gitignore
```

## Setup

### Prerequisites

- Python 3.11 (not 3.14, incompatible with blimpy)
- git

### Install

```bash
# Clone this repo
git clone https://github.com/w4gon79/backyard-seti.git
cd backyard-seti

# Install Berkeley SETI tools
pip install blimpy turbo_seti
```

### Python Path

On this machine, use Python 3.11 explicitly:
```
C:\Users\w4gon\AppData\Local\Programs\Python\Python311\python.exe
```

## Usage

*(Scripts to be added as we build them)*

## License

MIT

## Links

- [Breakthrough Listen Open Data](https://breakthroughinitiatives.org/opendatasearch)
- [Berkeley SETI Research Center](https://seti.berkeley.edu)
- [blimpy documentation](https://blimpy.readthedocs.io)
- [turbo_seti documentation](https://turbo-seti.readthedocs.io)
