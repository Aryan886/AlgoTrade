type Props = {
  status: {
    engine_state: string;
  };
};

export default function IdleTradeEntry({ status }: Props) {
  return (
    <button
      disabled={status.engine_state !== "IDLE"}
      onClick={() => fetch("/manual_trade/preview", { method: "POST" })}
    >
      Preview Trade
    </button>
  );
}
