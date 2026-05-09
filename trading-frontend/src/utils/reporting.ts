import type {
  BacktestKpis,
  DataQualitySummary,
  EquityPoint,
  PnlRow,
  ShowcaseReportData,
  SlAnalysis,
  SummaryMetric,
  TradeRow,
} from "../types/dashboard";

const BASE_EQUITY = 100000;

export function formatCurrency(value: number) {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0,
  }).format(value);
}

export function formatPercent(value: number) {
  return `${(value * 100).toFixed(2)}%`;
}

export function buildShowcaseReportFromTrades(trades: TradeRow[]): ShowcaseReportData {
  const closed = trades.filter((trade) => typeof trade.realizedPnl === "number");
  const totalPnl = closed.reduce((sum, trade) => sum + (trade.realizedPnl ?? 0), 0);
  const wins = closed.filter((trade) => (trade.realizedPnl ?? 0) > 0);
  const losses = closed.filter((trade) => (trade.realizedPnl ?? 0) <= 0);
  const grossProfit = wins.reduce((sum, trade) => sum + (trade.realizedPnl ?? 0), 0);
  const grossLoss = Math.abs(losses.reduce((sum, trade) => sum + (trade.realizedPnl ?? 0), 0));
  const winRate = closed.length ? wins.length / closed.length : 0;
  const avgWin = wins.length ? grossProfit / wins.length : 0;
  const avgLoss = losses.length ? losses.reduce((sum, trade) => sum + (trade.realizedPnl ?? 0), 0) / losses.length : 0;
  const expectancy = (winRate * avgWin) - ((1 - winRate) * Math.abs(avgLoss));

  const equityCurve = buildEquityCurve(closed);
  const maxDrawdown = calculateMaxDrawdown(equityCurve);
  const slAnalysis = buildSlAnalysis(closed);
  const dataQuality: DataQualitySummary = {
    skippedEntries: 0,
    warnings: [],
    healthy: true,
  };

  const kpis: BacktestKpis = {
    totalPnl,
    trades: closed.length,
    winRate,
    maxDrawdown,
    dailySharpe: closed.length > 1 ? totalPnl / closed.length / 100 : 0,
    expectancy,
  };

  const summary: SummaryMetric[] = [
    { metric: "Total P&L", value: totalPnl, formattedValue: formatCurrency(totalPnl) },
    { metric: "Number of Trades", value: closed.length, formattedValue: String(closed.length) },
    { metric: "Win Rate", value: winRate, formattedValue: formatPercent(winRate) },
    { metric: "Avg Win", value: avgWin, formattedValue: formatCurrency(avgWin) },
    { metric: "Avg Loss", value: avgLoss, formattedValue: formatCurrency(avgLoss) },
    { metric: "Profit Factor", value: grossLoss === 0 ? "inf" : (grossProfit / grossLoss).toFixed(2), formattedValue: grossLoss === 0 ? "inf" : (grossProfit / grossLoss).toFixed(2) },
    { metric: "Max Drawdown", value: maxDrawdown, formattedValue: formatCurrency(maxDrawdown) },
    { metric: "Expectancy", value: expectancy, formattedValue: formatCurrency(expectancy) },
  ];

  return {
    summary,
    kpis,
    equityCurve,
    slAnalysis,
    dataQuality,
    trades,
  };
}

export function buildSummaryCards(rows: PnlRow[]) {
  const totalPnl = rows.reduce((sum, row) => sum + row.pnl, 0);
  const totalTrades = rows.reduce((sum, row) => sum + row.tradeCount, 0);
  const averageWinRate = rows.length
    ? rows.reduce((sum, row) => sum + row.winRate, 0) / rows.length
    : 0;
  return { totalPnl, totalTrades, averageWinRate };
}

function buildEquityCurve(trades: TradeRow[]): EquityPoint[] {
  let runningEquity = BASE_EQUITY;
  return trades.map((trade) => {
    runningEquity += trade.realizedPnl ?? 0;
    return {
      timestamp: trade.exitTs ?? trade.entryTs ?? `trade-${trade.id}`,
      equity: runningEquity,
    };
  });
}

function calculateMaxDrawdown(points: EquityPoint[]) {
  let peak = BASE_EQUITY;
  let maxDrawdown = 0;

  for (const point of points) {
    peak = Math.max(peak, point.equity);
    maxDrawdown = Math.max(maxDrawdown, peak - point.equity);
  }

  return maxDrawdown;
}

function buildSlAnalysis(trades: TradeRow[]): SlAnalysis {
  const slHits = trades.filter((trade) => trade.exitReason === "SL_HIT");
  const timeExits = trades.filter((trade) => trade.exitReason === "TIME_EXIT");
  const totalAdjustments = trades.reduce((sum, trade) => sum + (trade.slAdjustments ?? 0), 0);
  const avgAdjustmentsPerTrade = trades.length ? totalAdjustments / trades.length : 0;
  const avgSlMovementPct = trades.length
    ? trades.reduce((sum, trade) => {
        if (!trade.slInitial || !trade.slFinal) {
          return sum;
        }
        return sum + (((trade.slInitial - trade.slFinal) / trade.slInitial) * 100);
      }, 0) / trades.length
    : 0;

  const byLot = new Map<string, number>();
  for (const trade of slHits) {
    const lotLabel = trade.positionType ?? "n/a";
    byLot.set(lotLabel, (byLot.get(lotLabel) ?? 0) + 1);
  }

  return {
    totalAdjustments,
    avgAdjustmentsPerTrade,
    avgSlMovementPct,
    tradesHitSl: slHits.length,
    tradesTimeExit: timeExits.length,
    totalTrades: trades.length,
    slByLot: Array.from(byLot.entries()).map(([lotId, count]) => ({ lotId, count })),
  };
}
