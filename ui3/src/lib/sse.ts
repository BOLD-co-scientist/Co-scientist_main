import type { Ev } from "./types";

export interface StreamOpts {
  url: string;
  token: string;
  sinceId?: string;
  onEvent: (ev: Ev) => void;
  onOpen?: () => void;
  onError?: (e: unknown) => void;
  onUnauthorized?: () => void;
}

/**
 * Fetch-based SSE reader (brief §7.1). The native EventSource API cannot set an
 * Authorization header, so we stream the body ourselves and parse `data:` lines.
 * Returns a close() function that aborts the connection.
 */
export function streamEvents(opts: StreamOpts): () => void {
  const controller = new AbortController();

  (async () => {
    const u = new URL(opts.url, window.location.origin);
    if (opts.sinceId) u.searchParams.set("since", opts.sinceId);

    try {
      const res = await fetch(u.toString(), {
        method: "GET",
        headers: {
          Authorization: `Bearer ${opts.token}`,
          Accept: "text/event-stream",
        },
        signal: controller.signal,
      });

      if (res.status === 401) {
        opts.onUnauthorized?.();
        throw new Error("SSE 401");
      }
      if (!res.ok || !res.body) throw new Error(`SSE ${res.status}`);
      opts.onOpen?.();

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";

      // eslint-disable-next-line no-constant-condition
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });

        let sep: number;
        // SSE messages are separated by a blank line.
        while ((sep = buf.indexOf("\n\n")) >= 0) {
          const chunk = buf.slice(0, sep);
          buf = buf.slice(sep + 2);
          const dataLine = chunk
            .split("\n")
            .find((l) => l.startsWith("data:"));
          if (!dataLine) continue;
          const json = dataLine.slice(5).trim();
          if (!json) continue;
          try {
            opts.onEvent(JSON.parse(json) as Ev);
          } catch {
            /* ignore malformed frame */
          }
        }
      }
    } catch (e) {
      if (!controller.signal.aborted) opts.onError?.(e);
    }
  })();

  return () => controller.abort();
}
