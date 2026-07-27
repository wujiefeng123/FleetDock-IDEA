# FleetDock IDEA brand assets

The packaging icons and Windows installer sidebar use the reviewed FleetDock
docking-gateway geometry in `src/assets/icon.svg` and
`scripts/generate_fleetdock_brand_assets.py`. The interlocking code paths and
three connection nodes represent one IDE serving multiple autonomous platforms.
The committed raster files are the release inputs. Maintainers who need to
regenerate them can install Pillow in a separate tooling environment and run:

```powershell
python scripts/generate_fleetdock_brand_assets.py
```

The generator writes `dist-resources/icon.svg`, `dist-resources/icon.png`,
`dist-resources/icons/512x512.png`, `dist-resources/icons/icon.ico`, and
`dist-resources/installerSidebar.bmp`. Packaging itself does not invoke the
generator and therefore adds no dependency to the upstream build environment.
