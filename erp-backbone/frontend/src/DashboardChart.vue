<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { init, use, type EChartsType } from 'echarts/core'
import { BarChart, GaugeChart, LineChart } from 'echarts/charts'
import { AriaComponent, GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { locale, t } from './i18n'
import type { ChartSeries, Link } from './dashboard'

use([BarChart, GaugeChart, LineChart, AriaComponent, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])
const props = defineProps<{
  title: string; kind: 'line' | 'bar' | 'gauge'; series: ChartSeries[]
  theme: string; currency?: string
}>()
const emit = defineEmits<{open: [link: Link]}>()
const surface = ref<HTMLDivElement>()
let chart: EChartsType | undefined
let observer: ResizeObserver | undefined
let themeObserver: MutationObserver | undefined
let drawFrame = 0
let frame = 0
let width = 0
let height = 0

function draw() {
  if (!chart || !surface.value) return
  const styles = getComputedStyle(surface.value)
  const ink = styles.getPropertyValue('--ink').trim()
  const muted = styles.getPropertyValue('--muted').trim()
  const border = styles.getPropertyValue('--border').trim()
  const primary = styles.getPropertyValue('--primary').trim()
  const secondary = styles.getPropertyValue('--chart-secondary').trim()
  const number = new Intl.NumberFormat(locale.value, {notation: 'compact', maximumFractionDigits: 1})
  const series = props.series
  const axisLabels = series[0]?.points.map(p => p.label) ?? []
  const rtl = locale.value === 'ar'
  const legend = props.kind === 'line'
  const gauge = props.kind === 'gauge'
  const tooltip = (payload: unknown) => {
    const events = (Array.isArray(payload) ? payload : [payload]) as {seriesIndex: number; dataIndex: number}[]
    return events.flatMap(event => {
      const point = series[event.seriesIndex]?.points[event.dataIndex]
      if (!point) return []
      return [point.label + ' · ' + series[event.seriesIndex]!.name + ' · ' + point.value + (props.currency ? ' ' + props.currency : '%')]
    }).join('\n')
  }
  chart.setOption({
    color: [primary, secondary], textStyle: {color: ink, fontFamily: styles.fontFamily},
    animation: !window.matchMedia('(prefers-reduced-motion: reduce)').matches,
    aria: {enabled: true, decal: {show: true}, label: {description: props.title + '. ' + t('Open the chart data table for values and reports.')}},
    tooltip: {trigger: gauge || props.kind === 'bar' ? 'item' : 'axis', renderMode: 'richText', confine: true, formatter: tooltip},
    ...(gauge ? {} : {
      legend: {show: legend, bottom: 0, textStyle: {color: ink}},
      grid: {top: 12, bottom: legend ? 54 : 32, left: 8, right: 8, containLabel: true},
      xAxis: {type: 'category', data: axisLabels, inverse: rtl, axisLabel: {color: muted, hideOverlap: true, interval: 'auto', overflow: 'truncate'}, axisTick: {show: false}, axisLine: {lineStyle: {color: border}}},
      yAxis: {type: 'value', position: rtl ? 'right' : 'left', axisLabel: {color: muted, formatter: (n: number) => number.format(n)}, splitLine: {lineStyle: {color: border}}},
    }),
    series: series.map((item, index) => gauge ? {
      type: 'gauge', name: item.name, min: 0, max: 100, center: ['50%', '62%'], radius: '91%',
      startAngle: 180, endAngle: 0, pointer: {show: false},
      axisLine: {lineStyle: {width: 15, color: [[1, border]]}}, progress: {show: true, width: 15},
      splitLine: {show: false}, axisTick: {show: false}, axisLabel: {show: false},
      detail: {color: ink, fontSize: 30, offsetCenter: [0, '-12%'], formatter: (value: number) => value.toFixed(1) + '%'},
      data: item.points.map(point => Number(point.value)),
    } : {
      type: props.kind, name: item.name, data: item.points.map(point => Number(point.value)),
      showSymbol: item.points.length <= 60, symbolSize: 6, barMaxWidth: 35,
      lineStyle: {width: 3, type: index % 2 ? 'dashed' : 'solid'},
    }),
  }, {notMerge: true})
}

function scheduleDraw() {
  cancelAnimationFrame(drawFrame)
  drawFrame = requestAnimationFrame(draw)
}

onMounted(() => {
  if (!surface.value) return
  chart = init(surface.value, undefined, {renderer: 'canvas'})
  chart.on('click', event => {
    if (event.seriesIndex === undefined || event.dataIndex === undefined) return
    const point = props.series[event.seriesIndex]?.points[event.dataIndex]
    if (point) emit('open', point.link)
  })
  draw()
  observer = new ResizeObserver(([entry]) => {
    if (!entry) return
    const nextWidth = Math.round(entry.contentRect.width)
    const nextHeight = Math.round(entry.contentRect.height)
    if (!nextWidth || !nextHeight || (width === nextWidth && height === nextHeight)) return
    width = nextWidth
    height = nextHeight
    cancelAnimationFrame(frame)
    frame = requestAnimationFrame(() => chart?.resize({width, height}))
  })
  observer.observe(surface.value)
  themeObserver = new MutationObserver(scheduleDraw)
  themeObserver.observe(document.documentElement, {attributes:true,attributeFilter:['data-theme']})
})
watch(() => [props.series, props.theme, locale.value], scheduleDraw, {flush:'post'})
onBeforeUnmount(() => {
  observer?.disconnect()
  themeObserver?.disconnect()
  cancelAnimationFrame(drawFrame)
  cancelAnimationFrame(frame)
  chart?.dispose()
  chart = undefined
})
</script>

<template>
  <div class="dashboard-chart" :class="{gauge:kind === 'gauge'}">
    <div ref="surface" class="dashboard-chart-surface" />
    <details v-if="kind !== 'gauge'" class="dashboard-chart-data">
      <summary>{{ t('Chart data') }}</summary>
      <div class="table-panel">
        <table>
          <thead><tr><th>{{ t(kind === 'bar' ? 'Aging bucket' : 'Period') }}</th><th v-for="item in series" :key="item.name">{{ item.name }}</th></tr></thead>
          <tbody><tr v-for="(point,index) in series[0]?.points" :key="index">
            <td>{{ point.label }}</td>
            <td v-for="item in series" :key="item.name">
              <RouterLink v-if="item.points[index]" :to="item.points[index]!.link"><bdi>{{ item.points[index]!.value }} {{ currency }}</bdi></RouterLink>
              <span v-else>—</span>
            </td>
          </tr></tbody>
        </table>
      </div>
    </details>
  </div>
</template>
