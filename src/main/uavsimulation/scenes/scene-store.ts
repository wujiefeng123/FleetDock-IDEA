// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

import { app } from 'electron';
import * as fs from 'fs';
import * as path from 'path';
import { UavSimulationScene, UavSimulationSceneState } from '../types';
import {
  getDefaultPx4Path,
  getDefaultSkyLabSimPath
} from '../config/simulation-install-paths';

const LEGACY_DEFAULT_PX4_PATH = '/PX4-Autopilot';
const LEGACY_DEFAULT_SCENE_PACKAGE_DIRECTORY = 'D:\\simscens';

// [UAV-IDE MODIFY] Persist user-registered Project AirSim scene directories in a standalone UAV simulation store.
export class UavSimulationSceneStore {
  load(): UavSimulationSceneState {
    return this._normalizeState(this._readState());
  }

  saveScene(scene: Partial<UavSimulationScene>): UavSimulationSceneState {
    const current = this.load();
    const now = new Date().toISOString();
    const id = String(scene.id || '').trim();
    const name = String(scene.name || '').trim();
    if (!name) {
      throw new Error('请填写场景名称。');
    }

    if (id) {
      const index = current.scenes.findIndex(item => item.id === id);
      if (index < 0) {
        throw new Error('未找到要编辑的场景。');
      }
      current.scenes[index] = {
        ...current.scenes[index],
        name,
        updatedAt: now
      };
      this._writeState(current);
      return current;
    }

    const executablePath = path.normalize(
      String(scene.executablePath || '').trim()
    );
    const rootDirectory = path.normalize(
      String(
        scene.rootDirectory ||
          scene.workingDirectory ||
          (executablePath ? path.dirname(executablePath) : '')
      ).trim()
    );
    const workingDirectory = path.normalize(
      String(
        scene.workingDirectory || rootDirectory || path.dirname(executablePath)
      ).trim()
    );
    if (!rootDirectory || !executablePath) {
      throw new Error('请先选择有效的 Project AirSim 场景目录。');
    }

    const existingIndex = current.scenes.findIndex(
      item => item.rootDirectory.toLowerCase() === rootDirectory.toLowerCase()
    );
    if (existingIndex >= 0) {
      current.scenes[existingIndex] = {
        ...current.scenes[existingIndex],
        name,
        rootDirectory,
        executablePath,
        workingDirectory,
        engine: 'project-airsim',
        updatedAt: now
      };
      current.selectedSceneId =
        current.selectedSceneId || current.scenes[existingIndex].id;
      this._writeState(current);
      return current;
    }

    const newScene: UavSimulationScene = {
      id: `scene-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      name,
      rootDirectory,
      executablePath,
      workingDirectory,
      engine: 'project-airsim',
      createdAt: now,
      updatedAt: now
    };
    current.scenes.push(newScene);
    current.selectedSceneId = current.selectedSceneId || newScene.id;
    this._writeState(current);
    return current;
  }

  saveConfig(config: {
    px4Path?: string;
    scenePackageDirectory?: string;
  }): UavSimulationSceneState {
    const current = this.load();
    current.px4Path = this._normalizePx4Path(config.px4Path || current.px4Path);
    current.scenePackageDirectory = this._normalizeScenePackageDirectory(
      config.scenePackageDirectory || current.scenePackageDirectory
    );
    this._writeState(current);
    return current;
  }

  saveScannedScenes(
    scenes: Partial<UavSimulationScene>[],
    scenePackageDirectory: string
  ): UavSimulationSceneState {
    const current = this.saveConfig({ scenePackageDirectory });
    const previousScenes = current.scenes;
    current.scenes = [];
    for (const scene of scenes) {
      this._upsertScannedScene(current, scene, previousScenes);
    }
    current.selectedSceneId = current.scenes.some(
      scene => scene.id === current.selectedSceneId
    )
      ? current.selectedSceneId
      : current.scenes[0]?.id || '';
    this._writeState(current);
    return current;
  }

  deleteScene(sceneId: string): UavSimulationSceneState {
    const current = this.load();
    const target = String(sceneId || '').trim();
    current.scenes = current.scenes.filter(scene => scene.id !== target);
    if (current.selectedSceneId === target) {
      current.selectedSceneId = current.scenes[0]?.id || '';
    }
    this._writeState(current);
    return current;
  }

  selectScene(sceneId: string): UavSimulationSceneState {
    const current = this.load();
    const target = String(sceneId || '').trim();
    if (target && !current.scenes.some(scene => scene.id === target)) {
      throw new Error('未找到要选择的场景。');
    }
    current.selectedSceneId = target;
    this._writeState(current);
    return current;
  }

  private _readState(): Partial<UavSimulationSceneState> {
    const storePath = this._storePath();
    if (!fs.existsSync(storePath)) {
      return {};
    }
    try {
      return JSON.parse(fs.readFileSync(storePath, 'utf8'));
    } catch (error) {
      console.error('Failed to read UAV simulation scene store:', error);
      return {};
    }
  }

  private _writeState(state: UavSimulationSceneState): void {
    const storePath = this._storePath();
    fs.mkdirSync(path.dirname(storePath), { recursive: true });
    fs.writeFileSync(
      storePath,
      JSON.stringify(this._normalizeState(state), null, 2),
      'utf8'
    );
  }

  private _normalizeState(
    state: Partial<UavSimulationSceneState>
  ): UavSimulationSceneState {
    const scenes = Array.isArray(state.scenes)
      ? state.scenes
          .map(scene => this._normalizeScene(scene))
          .filter((scene): scene is UavSimulationScene => !!scene)
      : [];
    const selectedSceneId = scenes.some(
      scene => scene.id === state.selectedSceneId
    )
      ? String(state.selectedSceneId || '')
      : scenes[0]?.id || '';
    return {
      scenes,
      selectedSceneId,
      px4Path: this._normalizePx4Path(state.px4Path),
      scenePackageDirectory: this._normalizeScenePackageDirectory(
        state.scenePackageDirectory
      )
    };
  }

  private _normalizePx4Path(px4Path: unknown): string {
    const normalized = String(px4Path || '').trim();
    return !normalized || normalized === LEGACY_DEFAULT_PX4_PATH
      ? getDefaultPx4Path()
      : path.normalize(normalized);
  }

  private _normalizeScenePackageDirectory(
    scenePackageDirectory: unknown
  ): string {
    const normalized = String(scenePackageDirectory || '').trim();
    const legacy = path
      .normalize(LEGACY_DEFAULT_SCENE_PACKAGE_DIRECTORY)
      .toLowerCase();
    return !normalized || path.normalize(normalized).toLowerCase() === legacy
      ? getDefaultSkyLabSimPath()
      : path.normalize(normalized);
  }

  private _upsertScannedScene(
    state: UavSimulationSceneState,
    scene: Partial<UavSimulationScene>,
    previousScenes: UavSimulationScene[] = state.scenes
  ): void {
    const now = new Date().toISOString();
    const executablePath = path.normalize(
      String(scene.executablePath || '').trim()
    );
    if (!executablePath) {
      return;
    }
    const previous = previousScenes.find(
      item => item.executablePath.toLowerCase() === executablePath.toLowerCase()
    );
    const nextScene: UavSimulationScene = {
      id:
        previous?.id ||
        `scene-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      name:
        previous?.name ||
        String(scene.name || '').trim() ||
        path.basename(executablePath, '.exe'),
      rootDirectory: path.normalize(
        String(scene.rootDirectory || path.dirname(executablePath))
      ),
      executablePath,
      workingDirectory: path.normalize(
        String(scene.workingDirectory || path.dirname(executablePath))
      ),
      engine: 'project-airsim',
      createdAt: previous?.createdAt || now,
      updatedAt: now
    };
    const index = state.scenes.findIndex(
      item => item.executablePath.toLowerCase() === executablePath.toLowerCase()
    );
    if (index >= 0) {
      state.scenes[index] = nextScene;
    } else {
      state.scenes.push(nextScene);
    }
  }

  private _normalizeScene(
    scene: Partial<UavSimulationScene>
  ): UavSimulationScene | null {
    const id = String(scene?.id || '').trim();
    const name = String(scene?.name || '').trim();
    const executablePath = String(scene?.executablePath || '').trim();
    if (!id || !name || !executablePath) {
      return null;
    }
    const now = new Date().toISOString();
    const rootDirectory =
      String(scene?.rootDirectory || '').trim() ||
      String(scene?.workingDirectory || '').trim() ||
      path.dirname(path.normalize(executablePath));
    return {
      id,
      name,
      rootDirectory: path.normalize(rootDirectory),
      executablePath: path.normalize(executablePath),
      workingDirectory:
        String(scene?.workingDirectory || '').trim() ||
        path.normalize(rootDirectory),
      engine: 'project-airsim',
      createdAt: String(scene?.createdAt || now),
      updatedAt: String(scene?.updatedAt || scene?.createdAt || now)
    };
  }

  private _storePath(): string {
    return path.join(app.getPath('userData'), 'uav-simulation', 'scenes.json');
  }
}
