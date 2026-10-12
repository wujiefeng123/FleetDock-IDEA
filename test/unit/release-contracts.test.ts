import { describe, expect, it } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';
import packageJson from '../../package.json';
import { fleetDockDesktopUpdateConfig } from '../../src/main/fleetdock/application-bootstrap';

const repositoryRoot = path.resolve(__dirname, '..', '..');

describe('FleetDock Windows release contracts', () => {
  it('uses the FleetDock package and operating-system identity', () => {
    expect(packageJson.name).toBe('fleetdock-idea');
    expect(packageJson.build.appId).toBe('com.fleetdock.idea');
    expect(packageJson.build.productName).toBe('FleetDock IDEA');
  });

  it('uses the RC version and versioned installer name', () => {
    expect(packageJson.version).toBe('4.6.2-2-rc.1');
    expect(packageJson.build.nsis.artifactName).toBe(
      'FleetDock-IDEA-${version}-win-${arch}-Setup.${ext}'
    );
  });

  it('starts the installed FleetDock executable from jlab.cmd', () => {
    const launcher = fs.readFileSync(
      path.join(repositoryRoot, 'dist-resources', 'win', 'jlab.cmd'),
      'utf8'
    );
    expect(launcher).toContain('"%~dp0FleetDock IDEA.exe" %*');
    expect(launcher).toContain('exit /b %ERRORLEVEL%');
    expect(launcher).not.toContain('JupyterLab.exe');
  });

  it('packages the staged QGroundControl application beside FleetDock IDEA', () => {
    expect(packageJson.build.win.extraFiles).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          from: 'dist-resources/win/lib/QGroundControl',
          to: 'lib/QGroundControl'
        })
      ])
    );
    expect(packageJson.scripts['dist:win-64']).toContain(
      'stage_qgroundcontrol:win-64'
    );
    expect(packageJson.build.win.signExts).toContain('!QGroundControl.exe');
  });

  it('disables product update networking until FleetDock owns an update service', () => {
    expect(fleetDockDesktopUpdateConfig).toEqual({ enabled: false });
    expect(packageJson.build.publish).toBeNull();
  });

  it('uses FleetDock product ownership without advertising upstream support channels', () => {
    expect(packageJson.author.name).toBe('FleetDock IDEA Team');
    expect(packageJson.build.copyright).toBe('Copyright © FleetDock IDEA Team');
    expect(packageJson).not.toHaveProperty('bugs');
    expect(packageJson).not.toHaveProperty('homepage');
    expect(packageJson).not.toHaveProperty('repository');
  });

  it('excludes development and agent-only files from app.asar', () => {
    const packagedFiles = packageJson.build.files;
    expect(packagedFiles).toEqual(
      expect.arrayContaining([
        '!src',
        '!scripts',
        '!test',
        '!test-results',
        '!delivery',
        '!*.md',
        '!AGENTS.md',
        '!.vscode'
      ])
    );
  });
});
