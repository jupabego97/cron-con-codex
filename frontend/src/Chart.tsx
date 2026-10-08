import { useMemo } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { money, number } from "./format";
type Row = Record<string, string | number | null>;

export default function Chart({
  title,
  data,
  dataKey,
  moneyValue = false,
}: {
  title: string;
  data: Row[];
  dataKey: string;
  moneyValue?: boolean;
}) {
  const rows = useMemo(
    () =>
      data.map<Row>((row) => ({
        ...row,
        label: String(row.label || row.period || ""),
      })),
    [data],
  );
  const currencies = [
    ...new Set(rows.map((row) => String(row.currency_code || "COP"))),
  ];
  if (moneyValue && currencies.length > 1)
    return (
      <>
        {currencies.map((currency) => (
          <Chart
            key={currency}
            title={`${title} · ${currency}`}
            data={data.filter(
              (row) => String(row.currency_code || "COP") === currency,
            )}
            dataKey={dataKey}
            moneyValue
          />
        ))}
      </>
    );
  if (!rows.length)
    return (
      <section className="chart-card">
        <h3>{title}</h3>
        <p className="muted">Sin datos para estos filtros.</p>
      </section>
    );
  const axis = { fill: "#c7d4ea", fontSize: 12 };
  const tooltip = {
    contentStyle: {
      background: "#101a2d",
      border: "1px solid #536b91",
      borderRadius: 8,
      color: "#ffffff",
    },
    labelStyle: { color: "#dbeafe" },
    itemStyle: { color: "#ffffff" },
  };
  const format = (value: unknown) =>
    moneyValue
      ? money(value as number, currencies[0] || "COP")
      : number(value as number);
  return (
    <section className="chart-card">
      <h3>{title}</h3>
      <div className="chart">
        {"period" in rows[0] ? (
          <ResponsiveContainer>
            <LineChart data={rows}>
              <CartesianGrid stroke="#334866" strokeDasharray="3 3" />
              <XAxis dataKey="label" tick={axis} />
              <YAxis tick={axis} />
              <Tooltip {...tooltip} formatter={format} />
              <Line
                type="monotone"
                dataKey={dataKey}
                stroke="#78f3d3"
                strokeWidth={2.5}
              />
            </LineChart>
          </ResponsiveContainer>
        ) : (
          <ResponsiveContainer>
            <BarChart data={rows}>
              <CartesianGrid stroke="#334866" strokeDasharray="3 3" />
              <XAxis dataKey="label" hide={rows.length > 8} tick={axis} />
              <YAxis tick={axis} />
              <Tooltip {...tooltip} formatter={format} />
              <Bar dataKey={dataKey} fill="#91a7ff" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        )}
      </div>
    </section>
  );
}
