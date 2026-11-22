export type Filters = Record<string, string>
export type Card = {filters: Filters; currencies: Record<string, string>}
export type Summary = {
  crm?: {count:number;currencies:Record<string,string>;filters:Filters}
  filters: Filters; today: string; inbox: number; ready_purchase_orders?: number
  cards: Partial<Record<'net-posted-sales' | 'grni', Card>>
}
export type TrendPoint = {from: string; to: string; value: string; filters: Filters}
export type Trend = {metric: 'net-posted-sales' | 'collections'; currency: string; points: TrendPoint[]}
export type Aging = { filters: Filters; series: {currency: string; total: string;
  points: {bucket: string; value: string; filters: Filters}[]}[] }
export type Performance = {filters: Filters; on_time: number; count: number}
export type Charts = {filters: Filters; widgets: {
  sales?: Trend[]; 'ap-aging'?: Aging; 'ar-aging'?: Aging; 'pm-on-time'?: Performance
}}
export type Link = {path: string; query?: Filters}
export type ChartSeries = {name: string; points: {label: string; value: string; link: Link}[]}
export const bucketLabels: Record<string, string> = {
  current: 'Not overdue', '1-30': '1–30 days', '31-60': '31–60 days',
  '61-90': '61–90 days', '90+': 'Over 90 days', credit: 'Credit balance',
}
