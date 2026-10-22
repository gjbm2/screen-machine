// One error boundary per layer (LIVE_V1 §2.2 rule 10, DCL I4): a crashing layer unmounts itself
// and leaves the black page behind it, never a white screen and never the whole page.
import React from "react";

interface Props {
  name: string;
  children?: React.ReactNode;
  /** Rendered instead of the children after an error. Default: nothing (the black page shows). */
  fallback?: React.ReactNode;
  /** When this value changes, a failed layer is given another chance. */
  resetKey?: unknown;
  onError?: (error: unknown) => void;
}

interface State {
  failed: boolean;
  resetKey: unknown;
}

export class LayerBoundary extends React.Component<Props, State> {
  state: State = { failed: false, resetKey: this.props.resetKey };

  static getDerivedStateFromError(): Partial<State> {
    return { failed: true };
  }

  static getDerivedStateFromProps(props: Props, state: State): Partial<State> | null {
    if (props.resetKey !== state.resetKey) return { failed: false, resetKey: props.resetKey };
    return null;
  }

  componentDidCatch(error: unknown, info: React.ErrorInfo): void {
    console.error(`[DisplayPage] layer "${this.props.name}" crashed; unmounted it`, error, info?.componentStack);
    try {
      this.props.onError?.(error);
    } catch {
      /* never rethrow from a boundary */
    }
  }

  render(): React.ReactNode {
    if (this.state.failed) return this.props.fallback ?? null;
    return this.props.children ?? null;
  }
}
