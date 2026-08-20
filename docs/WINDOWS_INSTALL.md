\# AstroFrame — Windows installation



AstroFrame 1.0 is distributed for Windows as a ZIP archive containing the application and its supporting files.



\## Requirements



\* 64-bit Windows 10 or Windows 11

\* ASTAP installed locally for plate solving

\* An ASTAP star database; D50 is recommended for normal AstroFrame use

\* Internet access is useful for Astrometry.net fallback solving, but is not required when ASTAP can solve the image locally



\## 1. Install ASTAP



Download and install the 64-bit Windows version of ASTAP.



The normal installation location is:



`C:\\Program Files\\astap`



AstroFrame also checks:



`C:\\Program Files (x86)\\astap`



Install the D50 star database as well.



AstroFrame does not currently bundle ASTAP or its star database.



\## 2. Install AstroFrame



Download the Windows ZIP package:



`AstroFrame-1.0.0-Windows-x64.zip`



Extract the ZIP to a normal folder on your computer. Do not run AstroFrame directly from inside the ZIP archive.



Inside the extracted `AstroFrame` folder, launch:



`AstroFrame.exe`



Keep the `AstroFrame.exe` file and the `\_internal` folder together. The application will not work correctly if only the EXE is copied elsewhere.



\## 3. First launch



Windows may display a security warning for an unsigned application downloaded from the internet.



If Windows SmartScreen appears, inspect the warning and use \*\*More info → Run anyway\*\* if you are satisfied that the package came from the AstroFrame project.



The private RC1 Windows package is not currently code-signed.



\## 4. Plate solving



AstroFrame first attempts to solve reference images locally with ASTAP.



On Windows it automatically checks the normal ASTAP installation locations and also looks for `astap` on the system PATH.



If ASTAP cannot solve an image, AstroFrame may ask for a target-name or RA/Dec hint or use Astrometry.net as a fallback.



\## 5. Updating AstroFrame



For a newer private-test build, extract the new ZIP into a new folder rather than copying individual files over an older installation.



Your AstroFrame user data and saved settings are stored separately from the application package.



\## Troubleshooting



\### AstroFrame says ASTAP was not found



Confirm that ASTAP is installed and that this file exists:



`C:\\Program Files\\astap\\astap.exe`



or:



`C:\\Program Files (x86)\\astap\\astap.exe`



\### AstroFrame opens but plate solving fails



Confirm that an ASTAP star database such as D50 is installed. Try the same image again and provide a target hint if AstroFrame requests one.



\### AstroFrame.exe does not start after being moved



Restore the complete extracted `AstroFrame` folder. `AstroFrame.exe` depends on files inside its accompanying `\_internal` folder.



\## Package verification



A `.sha256` file is supplied alongside the Windows ZIP so that the downloaded archive can be checked against its published SHA-256 checksum.



