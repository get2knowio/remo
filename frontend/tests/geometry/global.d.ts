// The handle tests/geometry/harness/main.tsx publishes for the specs to drive.
// Declared here rather than imported so a spec never pulls the harness (and
// with it React, xterm and the whole app) into the Node-side test process.

interface GeometryCardStats {
  id: string;
  visible: boolean;
  surfaceHeight: number;
  screenHeight: number;
  paintedRows: number;
}

interface GeometryHandle {
  setChrome(show: boolean): Promise<void>;
  maximize(id: string | null): Promise<void>;
  settle(): Promise<void>;
  stats(): GeometryCardStats[];
}

declare global {
  interface Window {
    __geometry: GeometryHandle;
  }
}

export {};
