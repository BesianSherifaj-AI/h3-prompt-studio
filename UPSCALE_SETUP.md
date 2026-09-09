# Open the existing UPSCALE app

Studio's optional UPSCALE action opens the separate desktop application you already installed. It adds the selected finished video and leaves that app's settings and processing controls intact. Choose scale, target frame rate, quality and other options in UPSCALE, then press **Start pending** there. Merely opening its window does not start video processing.

The integration looks for `UPSCALE` in your OneDrive Desktop or Desktop folder. To use another existing installation, set its folder before launching Studio:

```powershell
$env:H3_STUDIO_UPSCALE = 'D:\Apps\UPSCALE'
.\Launch.ps1
```

The folder must contain `main.py`, `backend.py`, `engine`, and its own `.venv\Scripts\pythonw.exe`. Studio does not install, modify or redistribute this external engine. The inspected GUI accepts video paths on its command line; its single-instance mechanism adds those files to an existing window instead of opening a second copy.

This GUI processes **video only**. The integration does not convert still images into fake video to claim image-upscaler support. Originals remain unchanged; completed upscale outputs and detailed processing reports stay in the output folder selected inside UPSCALE. H3 and the external app have separate processing controls, so finish active H3 work before starting an upscale job that needs the same GPU.
