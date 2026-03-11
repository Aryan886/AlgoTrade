
import type { EngineStatus } from "../../types/engine";

type Props = {
  status: EngineStatus;
};

export default function TradePreview({ status }: Props) {
  if (!status.preview) return null;

  return (
    <>
      <h2>Preview</h2>
      <p>Margin: {status.preview.estimated_margin}</p>
      <p>Max loss: {status.preview.max_loss}</p>
      <p>Expires at: {status.preview.expires_at}</p>
    </>
  );
}