export type WhepPlayerState = 'offline' | 'connecting' | 'online' | 'error';

export interface WhepPlayerDependencies {
  fetch: typeof fetch;
  createPeerConnection: () => RTCPeerConnection;
  resolveUrl: (url: string, baseUrl: string) => string;
}

export interface WhepPlayerOptions {
  video: HTMLVideoElement;
  dependencies?: Partial<WhepPlayerDependencies>;
  iceGatheringTimeoutMs?: number;
  videoTrackTimeoutMs?: number;
  onStateChange?: (state: WhepPlayerState, error?: Error) => void;
}

const DEFAULT_ICE_GATHERING_TIMEOUT_MS = 8_000;
const DEFAULT_VIDEO_TRACK_TIMEOUT_MS = 10_000;
const HAVE_CURRENT_DATA = 2;

/**
 * Demo 内部使用的 WHEP 播放器。
 *
 * 使用非 Trickle ICE：先等待浏览器收集完整候选，再一次性向 WHEP
 * 端点提交 SDP。控制 WebSocket 与此播放器没有生命周期依赖。
 */
export class WhepPlayer {
  readonly #video: HTMLVideoElement;
  readonly #dependencies: WhepPlayerDependencies;
  readonly #iceGatheringTimeoutMs: number;
  readonly #videoTrackTimeoutMs: number;
  readonly #onStateChange?: WhepPlayerOptions['onStateChange'];

  #peer?: RTCPeerConnection;
  #sessionUrl?: string;
  #closePromise?: Promise<void>;
  #cancelVideoWait?: () => void;

  constructor(options: WhepPlayerOptions) {
    this.#video = options.video;
    this.#dependencies = {
      fetch: options.dependencies?.fetch ?? globalThis.fetch.bind(globalThis),
      createPeerConnection:
        options.dependencies?.createPeerConnection ??
        (() => new RTCPeerConnection()),
      resolveUrl:
        options.dependencies?.resolveUrl ??
        ((url, baseUrl) =>
          new URL(
            url,
            new URL(baseUrl, globalThis.location?.href ?? 'http://localhost/')
          ).toString())
    };
    this.#iceGatheringTimeoutMs =
      options.iceGatheringTimeoutMs ?? DEFAULT_ICE_GATHERING_TIMEOUT_MS;
    this.#videoTrackTimeoutMs =
      options.videoTrackTimeoutMs ?? DEFAULT_VIDEO_TRACK_TIMEOUT_MS;
    this.#onStateChange = options.onStateChange;
  }

  async connect(whepUrl: string): Promise<void> {
    const endpoint = whepUrl.trim();
    if (!endpoint) throw new Error('请填写 WHEP 播放地址。');

    await this.close();
    this.#emitState('connecting');

    const peer = this.#dependencies.createPeerConnection();
    this.#peer = peer;
    peer.addTransceiver('video', { direction: 'recvonly' });
    const firstVideoFrame = waitForFirstVideoFrame(peer, this.#video);
    this.#cancelVideoWait = firstVideoFrame.cancel;

    try {
      const offer = await peer.createOffer();
      await peer.setLocalDescription(offer);
      await waitForIceGatheringComplete(peer, this.#iceGatheringTimeoutMs);

      const sdp = peer.localDescription?.sdp;
      if (!sdp) throw new Error('浏览器没有生成可提交的本地 SDP。');

      const response = await this.#dependencies.fetch(endpoint, {
        method: 'POST',
        headers: {
          Accept: 'application/sdp',
          'Content-Type': 'application/sdp'
        },
        body: sdp
      });
      if (response.status !== 200 && response.status !== 201) {
        throw new Error(
          `WHEP 建立会话失败：HTTP ${response.status} ${response.statusText}`.trim()
        );
      }

      const answerSdp = await response.text();
      if (!answerSdp.trim()) throw new Error('WHEP 服务没有返回 SDP Answer。');
      const location = response.headers.get('Location');
      this.#sessionUrl = location
        ? this.#dependencies.resolveUrl(location, endpoint)
        : undefined;
      await peer.setRemoteDescription({ type: 'answer', sdp: answerSdp });
      await waitWithTimeout(
        firstVideoFrame.promise,
        this.#videoTrackTimeoutMs,
        `等待首个视频帧超时（${this.#videoTrackTimeoutMs} ms）。`
      );
      this.#cancelVideoWait = undefined;
      this.#emitState('online');
    } catch (error) {
      const failure = toError(error);
      await this.#closeResources();
      this.#emitState('error', failure);
      throw failure;
    }
  }

  /**
   * 关闭媒体轨道和 WHEP 会话。可重复调用，重复调用不会再次发送 DELETE。
   */
  close(): Promise<void> {
    if (this.#closePromise) return this.#closePromise;
    this.#closePromise = this.#closeResources().finally(() => {
      this.#closePromise = undefined;
      this.#emitState('offline');
    });
    return this.#closePromise;
  }

  async #closeResources(): Promise<void> {
    const peer = this.#peer;
    const sessionUrl = this.#sessionUrl;
    const cancelVideoWait = this.#cancelVideoWait;
    this.#peer = undefined;
    this.#sessionUrl = undefined;
    this.#cancelVideoWait = undefined;
    cancelVideoWait?.();
    this.#video.srcObject = null;
    peer?.close();

    if (sessionUrl) {
      try {
        await this.#dependencies.fetch(sessionUrl, { method: 'DELETE' });
      } catch {
        // 页面退出或网络中断时，远端会话清理由服务端超时兜底。
      }
    }
  }

  #emitState(state: WhepPlayerState, error?: Error): void {
    this.#onStateChange?.(state, error);
  }
}

function waitForFirstVideoFrame(
  peer: RTCPeerConnection,
  video: HTMLVideoElement
): { promise: Promise<void>; cancel: () => void } {
  let resolveWait: (() => void) | undefined;
  let rejectWait: ((error: Error) => void) | undefined;
  let videoFrameCallbackId: number | undefined;
  let settled = false;

  const promise = new Promise<void>((resolve, reject) => {
    resolveWait = resolve;
    rejectWait = reject;
  });
  // SDP/HTTP 可能在进入首帧等待前失败；提前登记拒绝处理，避免清理取消时
  // 产生未处理 Promise，同时保留原 Promise 的拒绝语义给后续等待者。
  void promise.catch(() => undefined);
  const mediaEvents: Array<keyof HTMLMediaElementEventMap> = [
    'loadeddata',
    'canplay',
    'playing'
  ];

  const cleanup = () => {
    peer.removeEventListener('track', handleTrack);
    mediaEvents.forEach(eventName => {
      video.removeEventListener(eventName, handleMediaProgress);
    });
    if (
      videoFrameCallbackId !== undefined &&
      typeof video.cancelVideoFrameCallback === 'function'
    ) {
      video.cancelVideoFrameCallback(videoFrameCallbackId);
    }
    videoFrameCallbackId = undefined;
  };

  const finishIfPlayable = (): boolean => {
    if (
      settled ||
      video.readyState < HAVE_CURRENT_DATA ||
      video.videoWidth <= 0 ||
      video.videoHeight <= 0
    ) {
      return false;
    }
    settled = true;
    cleanup();
    resolveWait?.();
    return true;
  };

  const requestNextVideoFrame = () => {
    if (
      settled ||
      typeof video.requestVideoFrameCallback !== 'function' ||
      videoFrameCallbackId !== undefined
    ) {
      return;
    }
    videoFrameCallbackId = video.requestVideoFrameCallback(() => {
      videoFrameCallbackId = undefined;
      if (!finishIfPlayable()) requestNextVideoFrame();
    });
  };

  const handleMediaProgress = () => {
    if (!finishIfPlayable()) requestNextVideoFrame();
  };

  const handleTrack = (event: RTCTrackEvent) => {
    if (event.track.kind !== 'video') return;
    peer.removeEventListener('track', handleTrack);
    const stream = event.streams[0] ?? new MediaStream([event.track]);
    video.srcObject = stream;
    mediaEvents.forEach(eventName => {
      video.addEventListener(eventName, handleMediaProgress);
    });
    void video.play().catch(() => undefined);
    if (!finishIfPlayable()) requestNextVideoFrame();
  };

  peer.addEventListener('track', handleTrack);

  return {
    promise,
    cancel: () => {
      if (settled) return;
      settled = true;
      cleanup();
      rejectWait?.(new Error('视频首帧等待已取消。'));
    }
  };
}

function waitForIceGatheringComplete(
  peer: RTCPeerConnection,
  timeoutMs: number
): Promise<void> {
  if (peer.iceGatheringState === 'complete') return Promise.resolve();

  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      cleanup();
      reject(new Error(`ICE 候选收集超时（${timeoutMs} ms）。`));
    }, timeoutMs);
    const handleStateChange = () => {
      if (peer.iceGatheringState !== 'complete') return;
      cleanup();
      resolve();
    };
    const cleanup = () => {
      clearTimeout(timeout);
      peer.removeEventListener('icegatheringstatechange', handleStateChange);
    };
    peer.addEventListener('icegatheringstatechange', handleStateChange);
  });
}

function waitWithTimeout(
  operation: Promise<void>,
  timeoutMs: number,
  timeoutMessage: string
): Promise<void> {
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(
      () => reject(new Error(timeoutMessage)),
      timeoutMs
    );
    operation.then(
      () => {
        clearTimeout(timeout);
        resolve();
      },
      error => {
        clearTimeout(timeout);
        reject(error);
      }
    );
  });
}

function toError(error: unknown): Error {
  return error instanceof Error ? error : new Error(String(error));
}
