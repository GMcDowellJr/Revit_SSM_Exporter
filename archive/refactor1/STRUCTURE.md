ssm_exporter/
├── __init__.py
├── core/
│   ├── config.py           # Configuration management
│   ├── types.py            # Enums, dataclasses for occupancy states
│   └── logger.py           # Logger class
├── geometry/
│   ├── silhouette.py       # SilhouetteExtractor
│   ├── transforms.py       # View-basis coordinate transforms
│   └── grid.py             # Grid building and cell management
├── revit/
│   ├── collection.py       # Element collection from Revit
│   ├── views.py            # View processing
│   └── links.py            # Link model handling
├── processing/
│   ├── projection.py       # 3D→2D projection
│   ├── regions.py          # Region building and classification
│   ├── rasterization.py    # Region to cell rasterization
│   └── occupancy.py        # Occupancy computation
├── export/
│   ├── csv.py              # CSV export (already started!)
│   └── visualization.py    # PNG debug output
└── main.py                 # Entry point
