// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

// [UAV-IDE MODIFY] Build CodeAppCenter-compatible source packages from the active desktop workspace.

import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import * as zlib from 'zlib';
import {
  createDefaultApplicationConfig,
  createPublishApplicationConfig,
  readApplicationConfig
} from '../config/application';
import {
  CodeAppDependencyScanResult,
  CodeAppPackageResult,
  CodeAppPublishRequest
} from './types';

const REQUIREMENT_PATTERN = /^[A-Za-z0-9_.-]+(?:\[[A-Za-z0-9_.,-]+])?(?:\s*@\s*(?:git\+)?https?:\/\/\S+)?(?:\s*(?:(?:===|==|!=|~=|>=|<=|>|<)\s*[^,;\s]+)(?:\s*,\s*(?:===|==|!=|~=|>=|<=|>|<)\s*[^,;\s]+)*)?(?:\s*;\s*.+)?$/;
const IMPORT_PACKAGE_MAP: { [key: string]: string } = {
  cv2: 'opencv-python',
  PIL: 'Pillow',
  yaml: 'PyYAML',
  sklearn: 'scikit-learn'
};
const STDLIB = new Set([
  'abc',
  'argparse',
  'asyncio',
  'base64',
  'collections',
  'contextlib',
  'csv',
  'datetime',
  'decimal',
  'functools',
  'glob',
  'hashlib',
  'http',
  'io',
  'itertools',
  'json',
  'logging',
  'math',
  'os',
  'pathlib',
  'random',
  're',
  'shutil',
  'socket',
  'sqlite3',
  'statistics',
  'string',
  'subprocess',
  'sys',
  'tempfile',
  'threading',
  'time',
  'typing',
  'urllib',
  'uuid',
  'zipfile'
]);

export function readRequirementsText(workingDirectory: string): string {
  const requirementsPath = path.join(workingDirectory, 'requirements.txt');
  if (!fs.existsSync(requirementsPath)) {
    return '';
  }
  return fs.readFileSync(requirementsPath, 'utf8');
}

export function validateRequirementsText(text: string): void {
  const lines = String(text || '').split(/\r?\n/);
  let dependencyCount = 0;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    if (!line || line.startsWith('#')) {
      continue;
    }
    const lower = line.toLowerCase();
    if (
      lower.startsWith('-r ') ||
      lower.startsWith('--requirement ') ||
      lower.startsWith('-c ') ||
      lower.startsWith('--constraint ') ||
      lower.startsWith('-e ') ||
      lower.startsWith('--editable ') ||
      lower.startsWith('-f ') ||
      lower.startsWith('--find-links ') ||
      lower.startsWith('file:') ||
      line.startsWith('./') ||
      line.startsWith('../') ||
      line.startsWith('/') ||
      /^[A-Za-z]:[\\/]/.test(line)
    ) {
      throw new Error(`requirements.txt 第 ${i + 1} 行不符合应用中心规则`);
    }
    if (
      lower.startsWith('--index-url ') ||
      lower.startsWith('--extra-index-url ') ||
      lower.startsWith('--trusted-host ')
    ) {
      continue;
    }
    if (!REQUIREMENT_PATTERN.test(line)) {
      throw new Error(`requirements.txt 第 ${i + 1} 行依赖声明格式不支持`);
    }
    dependencyCount++;
  }
  if (dependencyCount === 0) {
    throw new Error('requirements.txt 至少需要声明一个依赖');
  }
}

export async function scanPythonDependencies(
  workingDirectory: string
): Promise<CodeAppDependencyScanResult> {
  const packages = new Set<string>();
  await walk(workingDirectory, async filePath => {
    if (path.extname(filePath).toLowerCase() !== '.py') {
      return;
    }
    const text = fs.readFileSync(filePath, 'utf8');
    const importRegex = /^\s*(?:import\s+([A-Za-z_][\w.]*)|from\s+([A-Za-z_][\w.]*)\s+import\s+)/gm;
    let match: RegExpExecArray | null;
    while ((match = importRegex.exec(text))) {
      const root = String(match[1] || match[2] || '').split('.')[0];
      if (!root || STDLIB.has(root) || root.startsWith('_')) {
        continue;
      }
      packages.add(IMPORT_PACKAGE_MAP[root] || root);
    }
  });
  return {
    packages: [...packages].sort(),
    requirementsText: [...packages].sort().join('\n')
  };
}

export async function createCodeAppPackage(
  request: CodeAppPublishRequest
): Promise<CodeAppPackageResult> {
  const workingDirectory = path.resolve(request.workingDirectory);
  if (!request.appCode || !request.appName || !request.version) {
    throw new Error('请先填写应用标识、应用名称和版本号');
  }
  if (request.mode === 'new-version' && !request.appId) {
    throw new Error('请选择要新增版本的已有应用');
  }
  const entryFile = path.join(workingDirectory, 'main.py');
  if (!fs.existsSync(entryFile)) {
    throw new Error('当前项目缺少 main.py，无法发布到应用中心');
  }
  validateRequirementsText(request.requirementsText);

  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'jld-codeapp-'));
  const stagingDir = path.join(tempRoot, 'source');
  fs.mkdirSync(stagingDir, { recursive: true });
  await copyWorkspace(workingDirectory, stagingDir);

  fs.writeFileSync(
    path.join(stagingDir, 'requirements.txt'),
    request.requirementsText.trimEnd() + '\n',
    'utf8'
  );

  const config =
    readApplicationConfig(workingDirectory) ||
    createDefaultApplicationConfig(workingDirectory);
  const publishConfig = createPublishApplicationConfig(
    {
      ...config,
      appCode: request.appCode,
      name: request.appName,
      description: request.description,
      version: request.version,
      changeLog: request.remark,
      workingDirectory
    },
    workingDirectory
  );
  fs.writeFileSync(
    path.join(stagingDir, 'desktop-app.json'),
    JSON.stringify(publishConfig, null, 2),
    'utf8'
  );

  const fileName = `${safeFilePart(
    request.appCode || request.appName
  )}-${safeFilePart(request.version)}.zip`;
  const zipPath = path.join(tempRoot, fileName);
  await zipDirectory(stagingDir, zipPath);

  return {
    zipPath,
    stagingDir,
    fileName
  };
}

async function copyWorkspace(source: string, target: string): Promise<void> {
  await walk(source, async sourcePath => {
    const relative = path.relative(source, sourcePath);
    if (shouldExclude(relative)) {
      return;
    }
    const targetPath = path.join(target, relative);
    fs.mkdirSync(path.dirname(targetPath), { recursive: true });
    fs.copyFileSync(sourcePath, targetPath);
  });
}

async function walk(
  root: string,
  onFile: (filePath: string) => Promise<void>
): Promise<void> {
  if (!fs.existsSync(root)) {
    return;
  }
  const entries = fs.readdirSync(root, { withFileTypes: true });
  for (const entry of entries) {
    const fullPath = path.join(root, entry.name);
    const relative = path.relative(root, fullPath);
    if (entry.isDirectory()) {
      if (shouldExclude(relative)) {
        continue;
      }
      await walk(fullPath, onFile);
    } else if (entry.isFile()) {
      await onFile(fullPath);
    }
  }
}

function shouldExclude(relative: string): boolean {
  const parts = relative.split(path.sep).filter(Boolean);
  return parts.some(part =>
    ['.git', '__pycache__', '.ipynb_checkpoints'].includes(part)
  );
}

function safeFilePart(value: string): string {
  return (
    String(value || 'app')
      .trim()
      .replace(/[^A-Za-z0-9_.-]+/g, '-')
      .replace(/^-+|-+$/g, '') || 'app'
  );
}

async function zipDirectory(sourceDir: string, zipPath: string): Promise<void> {
  // [UAV-IDE MODIFY] Generate ZIP packages in-process so publish does not depend on
  // platform shell tools such as Windows PowerShell Compress-Archive.
  const files = collectZipFiles(sourceDir);
  const chunks: Buffer[] = [];
  const centralDirectory: Buffer[] = [];
  let offset = 0;

  for (const file of files) {
    const source = fs.readFileSync(file.absolutePath);
    const compressed = zlib.deflateRawSync(source);
    const crc = crc32(source);
    const nameBuffer = Buffer.from(file.zipName, 'utf8');
    const { dosDate, dosTime } = toDosDateTime(
      fs.statSync(file.absolutePath).mtime
    );
    const localHeader = Buffer.alloc(30);
    localHeader.writeUInt32LE(0x04034b50, 0);
    localHeader.writeUInt16LE(20, 4);
    localHeader.writeUInt16LE(0x0800, 6);
    localHeader.writeUInt16LE(8, 8);
    localHeader.writeUInt16LE(dosTime, 10);
    localHeader.writeUInt16LE(dosDate, 12);
    localHeader.writeUInt32LE(crc, 14);
    localHeader.writeUInt32LE(compressed.length, 18);
    localHeader.writeUInt32LE(source.length, 22);
    localHeader.writeUInt16LE(nameBuffer.length, 26);
    localHeader.writeUInt16LE(0, 28);

    chunks.push(localHeader, nameBuffer, compressed);

    const centralHeader = Buffer.alloc(46);
    centralHeader.writeUInt32LE(0x02014b50, 0);
    centralHeader.writeUInt16LE(20, 4);
    centralHeader.writeUInt16LE(20, 6);
    centralHeader.writeUInt16LE(0x0800, 8);
    centralHeader.writeUInt16LE(8, 10);
    centralHeader.writeUInt16LE(dosTime, 12);
    centralHeader.writeUInt16LE(dosDate, 14);
    centralHeader.writeUInt32LE(crc, 16);
    centralHeader.writeUInt32LE(compressed.length, 20);
    centralHeader.writeUInt32LE(source.length, 24);
    centralHeader.writeUInt16LE(nameBuffer.length, 28);
    centralHeader.writeUInt16LE(0, 30);
    centralHeader.writeUInt16LE(0, 32);
    centralHeader.writeUInt16LE(0, 34);
    centralHeader.writeUInt16LE(0, 36);
    centralHeader.writeUInt32LE(0, 38);
    centralHeader.writeUInt32LE(offset, 42);
    centralDirectory.push(centralHeader, nameBuffer);

    offset += localHeader.length + nameBuffer.length + compressed.length;
  }

  const centralDirectoryOffset = offset;
  const centralDirectorySize = centralDirectory.reduce(
    (total, chunk) => total + chunk.length,
    0
  );
  const endOfCentralDirectory = Buffer.alloc(22);
  endOfCentralDirectory.writeUInt32LE(0x06054b50, 0);
  endOfCentralDirectory.writeUInt16LE(0, 4);
  endOfCentralDirectory.writeUInt16LE(0, 6);
  endOfCentralDirectory.writeUInt16LE(files.length, 8);
  endOfCentralDirectory.writeUInt16LE(files.length, 10);
  endOfCentralDirectory.writeUInt32LE(centralDirectorySize, 12);
  endOfCentralDirectory.writeUInt32LE(centralDirectoryOffset, 16);
  endOfCentralDirectory.writeUInt16LE(0, 20);

  fs.writeFileSync(
    zipPath,
    Buffer.concat([...chunks, ...centralDirectory, endOfCentralDirectory])
  );
}

function collectZipFiles(
  root: string
): Array<{ absolutePath: string; zipName: string }> {
  const files: Array<{ absolutePath: string; zipName: string }> = [];
  const visit = (directory: string): void => {
    const entries = fs
      .readdirSync(directory, { withFileTypes: true })
      .sort((a, b) => a.name.localeCompare(b.name));
    for (const entry of entries) {
      const fullPath = path.join(directory, entry.name);
      if (entry.isDirectory()) {
        visit(fullPath);
        continue;
      }
      if (!entry.isFile()) {
        continue;
      }
      files.push({
        absolutePath: fullPath,
        zipName: path.relative(root, fullPath).split(path.sep).join('/')
      });
    }
  };
  visit(root);
  return files;
}

function toDosDateTime(date: Date): { dosDate: number; dosTime: number } {
  const year = Math.max(date.getFullYear(), 1980);
  return {
    dosDate:
      ((year - 1980) << 9) | ((date.getMonth() + 1) << 5) | date.getDate(),
    dosTime:
      (date.getHours() << 11) |
      (date.getMinutes() << 5) |
      Math.floor(date.getSeconds() / 2)
  };
}

const CRC32_TABLE = new Uint32Array(256);
for (let i = 0; i < CRC32_TABLE.length; i++) {
  let value = i;
  for (let bit = 0; bit < 8; bit++) {
    value = value & 1 ? 0xedb88320 ^ (value >>> 1) : value >>> 1;
  }
  CRC32_TABLE[i] = value >>> 0;
}

function crc32(buffer: Buffer): number {
  let crc = 0xffffffff;
  for (const byte of buffer) {
    crc = CRC32_TABLE[(crc ^ byte) & 0xff] ^ (crc >>> 8);
  }
  return (crc ^ 0xffffffff) >>> 0;
}
