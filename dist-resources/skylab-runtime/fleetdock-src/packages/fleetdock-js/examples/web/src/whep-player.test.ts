import { describe, expect, it, vi } from 'vitest';
import { WhepPlayer } from './whep-player';

class FakePeerConnection extends EventTarget {
  iceGatheringState: RTCIceGatheringState = 'complete';
  localDescription: RTCSessionDescription | null = null;
  remoteDescription?: RTCSessionDescriptionInit;
  closed = false;
  transceivers: Array<[string, RTCRtpTransceiverInit]> = [];
  readonly emittedStream = {} as MediaStream;

  constructor(readonly emitVideoTrack = true) {
    super();
  }

  addTransceiver(kind: string, init: RTCRtpTransceiverInit): RTCRtpTransceiver {
    this.transceivers.push([kind, init]);
    return {} as RTCRtpTransceiver;
  }

  async createOffer(): Promise<RTCSessionDescriptionInit> {
    return { type: 'offer', sdp: 'v=0\r\no=fleetdock-offer' };
  }

  async setLocalDescription(
    description: RTCSessionDescriptionInit
  ): Promise<void> {
    this.localDescription = description as RTCSessionDescription;
  }

  async setRemoteDescription(
    description: RTCSessionDescriptionInit
  ): Promise<void> {
    this.remoteDescription = description;
    if (this.emitVideoTrack) {
      const trackEvent = new Event('track') as RTCTrackEvent;
      Object.defineProperties(trackEvent, {
        streams: { value: [this.emittedStream] },
        track: { value: { kind: 'video' } }
      });
      this.dispatchEvent(trackEvent);
    }
  }

  close(): void {
    this.closed = true;
  }
}

class FakeVideoElement extends EventTarget {
  readonly play = vi.fn().mockResolvedValue(undefined);
  readyState = 0;
  videoWidth = 0;
  videoHeight = 0;
  #srcObject: MediaProvider | null = null;

  constructor(readonly emitFirstFrame = true) {
    super();
  }

  get srcObject(): MediaProvider | null {
    return this.#srcObject;
  }

  set srcObject(value: MediaProvider | null) {
    this.#srcObject = value;
    if (!value || !this.emitFirstFrame) return;
    queueMicrotask(() => {
      this.readyState = 2;
      this.videoWidth = 1280;
      this.videoHeight = 720;
      this.dispatchEvent(new Event('loadeddata'));
    });
  }
}

function fakeVideo(emitFirstFrame = true): FakeVideoElement {
  return new FakeVideoElement(emitFirstFrame);
}

describe('WhepPlayer', () => {
  it.each([200, 201])(
    '使用非 Trickle SDP 建立 HTTP %i 会话，并按 Location 删除',
    async status => {
      const peer = new FakePeerConnection();
      const requests: Array<[string, RequestInit | undefined]> = [];
      const fetchMock = (vi.fn(
        async (url: string | URL | Request, init?: RequestInit) => {
          requests.push([String(url), init]);
          if (init?.method === 'DELETE') {
            return new Response(null, { status: 204 });
          }
          return new Response('v=0\r\no=fleetdock-answer', {
            status,
            headers: { Location: '/rtc/session/drone-001' }
          });
        }
      ) as unknown) as typeof fetch;
      const states: string[] = [];
      const player = new WhepPlayer({
        video: (fakeVideo() as unknown) as HTMLVideoElement,
        dependencies: {
          fetch: fetchMock,
          createPeerConnection: () => (peer as unknown) as RTCPeerConnection
        },
        onStateChange: state => states.push(state)
      });

      await player.connect(
        'https://live.example/rtc/v1/whep/?app=live&stream=drone-001'
      );

      expect(peer.transceivers).toEqual([['video', { direction: 'recvonly' }]]);
      expect(requests[0][1]).toMatchObject({
        method: 'POST',
        body: 'v=0\r\no=fleetdock-offer',
        headers: {
          Accept: 'application/sdp',
          'Content-Type': 'application/sdp'
        }
      });
      expect(peer.remoteDescription).toEqual({
        type: 'answer',
        sdp: 'v=0\r\no=fleetdock-answer'
      });
      expect(states).toContain('online');

      await Promise.all([player.close(), player.close()]);
      await player.close();

      expect(peer.closed).toBe(true);
      expect(requests.filter(([, init]) => init?.method === 'DELETE')).toEqual([
        ['https://live.example/rtc/session/drone-001', { method: 'DELETE' }]
      ]);
    }
  );

  it('HTTP 失败时关闭 PeerConnection 并报告错误', async () => {
    const peer = new FakePeerConnection();
    const states: Array<[string, string | undefined]> = [];
    const player = new WhepPlayer({
      video: (fakeVideo() as unknown) as HTMLVideoElement,
      dependencies: {
        fetch: (vi.fn(
          async () =>
            new Response('not found', { status: 404, statusText: 'Not Found' })
        ) as unknown) as typeof fetch,
        createPeerConnection: () => (peer as unknown) as RTCPeerConnection
      },
      onStateChange: (state, error) => states.push([state, error?.message])
    });

    await expect(player.connect('https://live.example/whep')).rejects.toThrow(
      'HTTP 404 Not Found'
    );
    expect(peer.closed).toBe(true);
    expect(states.at(-1)?.[0]).toBe('error');
  });

  it('收到视频轨道时绑定 MediaStream 并触发播放', async () => {
    const peer = new FakePeerConnection();
    const video = fakeVideo();
    const player = new WhepPlayer({
      video: (video as unknown) as HTMLVideoElement,
      dependencies: {
        fetch: (vi.fn(
          async () => new Response('v=0\r\no=fleetdock-answer', { status: 200 })
        ) as unknown) as typeof fetch,
        createPeerConnection: () => (peer as unknown) as RTCPeerConnection
      }
    });

    await player.connect('https://live.example/whep');

    expect(video.srcObject).toBe(peer.emittedStream);
    expect(video.play).toHaveBeenCalledOnce();
  });

  it('SDP 成功但没有视频轨道时超时、删除会话并进入错误状态', async () => {
    const peer = new FakePeerConnection(false);
    const requests: Array<[string, RequestInit | undefined]> = [];
    const states: string[] = [];
    const player = new WhepPlayer({
      video: (fakeVideo() as unknown) as HTMLVideoElement,
      videoTrackTimeoutMs: 10,
      dependencies: {
        fetch: (vi.fn(
          async (url: string | URL | Request, init?: RequestInit) => {
            requests.push([String(url), init]);
            if (init?.method === 'DELETE') {
              return new Response(null, { status: 204 });
            }
            return new Response('v=0\r\no=fleetdock-answer', {
              status: 201,
              headers: { Location: '/rtc/session/no-stream' }
            });
          }
        ) as unknown) as typeof fetch,
        createPeerConnection: () => (peer as unknown) as RTCPeerConnection
      },
      onStateChange: state => states.push(state)
    });

    await expect(player.connect('https://live.example/whep')).rejects.toThrow(
      '等待首个视频帧超时'
    );

    expect(peer.closed).toBe(true);
    expect(states.at(-1)).toBe('error');
    expect(requests.filter(([, init]) => init?.method === 'DELETE')).toEqual([
      ['https://live.example/rtc/session/no-stream', { method: 'DELETE' }]
    ]);
  });

  it('有远端视频轨道但没有可解码首帧时仍然超时', async () => {
    const peer = new FakePeerConnection(true);
    const video = fakeVideo(false);
    const states: string[] = [];
    const requests: Array<[string, RequestInit | undefined]> = [];
    const player = new WhepPlayer({
      video: (video as unknown) as HTMLVideoElement,
      videoTrackTimeoutMs: 10,
      dependencies: {
        fetch: (vi.fn(
          async (url: string | URL | Request, init?: RequestInit) => {
            requests.push([String(url), init]);
            if (init?.method === 'DELETE') {
              return new Response(null, { status: 204 });
            }
            return new Response('v=0\r\no=fleetdock-answer', {
              status: 201,
              headers: { Location: '/rtc/session/track-without-frame' }
            });
          }
        ) as unknown) as typeof fetch,
        createPeerConnection: () => (peer as unknown) as RTCPeerConnection
      },
      onStateChange: state => states.push(state)
    });

    await expect(player.connect('https://live.example/whep')).rejects.toThrow(
      '等待首个视频帧超时'
    );

    expect(video.srcObject).toBeNull();
    expect(peer.closed).toBe(true);
    expect(states).not.toContain('online');
    expect(states.at(-1)).toBe('error');
    expect(
      requests.filter(([, init]) => init?.method === 'DELETE')
    ).toHaveLength(1);
  });
});
