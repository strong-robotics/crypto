"use client"

import React, { useState, useEffect, useRef } from "react"
import { Area, AreaChart, XAxis, YAxis, ReferenceLine } from "recharts"
import dynamic from "next/dynamic"

interface ChartDataPoint {
  time: number
  value: number
}

type HistoryMarker = {
  value: number
  color?: string
}

export interface AreaChartComponentProps {
  timer: number
  width?: number
  height?: number
  chartData?: number[]
  forecastData?: number[]
  showForecast?: boolean
  entrySec?: number | null
  exitSec?: number | null
  historyMarkers?: HistoryMarker[]
  hasRealTrading?: boolean | null  // NULL = not checked, TRUE = has SWAP, FALSE = transfer only (gray color)
  medianAmountUsd?: number | null  // median trade size to highlight whales
}

function AreaChartComponent({ timer, width, height = 250, chartData: externalChartData, forecastData, showForecast = false, entrySec, exitSec, historyMarkers, hasRealTrading, medianAmountUsd }: AreaChartComponentProps) {
  // ДІАГНОСТИКА: логуємо вхідні параметри
  console.log(`🧪 AreaChartComponent props:`, {
    chartDataLength: externalChartData?.length || 0,
    forecastDataLength: forecastData?.length || 0,
    showForecast,
    entrySec,
    exitSec,
    hasRealTrading,
    chartDataFirst3: externalChartData?.slice(0, 3),
    forecastDataFirst3: forecastData?.slice(0, 3)
  });
  
  const isHighValue = typeof medianAmountUsd === "number" && medianAmountUsd > 100;
  // Визначаємо колір для градієнта та контуру
  const chartColor = hasRealTrading === false ? "#9ca3af" : isHighValue ? "#16a34a" : "#3b82f6";
  // Унікальний ID для градієнта, щоб React оновлював його при зміні кольору
  const gradientId = `fillValue-${hasRealTrading === false ? 'gray' : isHighValue ? 'green' : 'blue'}`;
  const containerRef = useRef<HTMLDivElement>(null)
  const [chartWidth, setChartWidth] = useState(width || 800)
  const [chartData, setChartData] = useState<ChartDataPoint[]>([])
  const [yAxisDomain, setYAxisDomain] = useState<[number, number]>([0, 100])
  const [needsScroll, setNeedsScroll] = useState(false)

  // Get actual container width and calculate chart width
  useEffect(() => {
    const updateWidth = () => {
      if (containerRef.current) {
        const containerWidth = containerRef.current.offsetWidth
        const clientWidth = containerRef.current.clientWidth
        const scrollWidth = containerRef.current.scrollWidth
        console.log(`📏 Container dimensions: offsetWidth=${containerWidth}px, clientWidth=${clientWidth}px, scrollWidth=${scrollWidth}px`)
        
        const fcUse = showForecast ? (forecastData?.length || 0) : 0
        const totalLength = (externalChartData?.length || 0) + fcUse
        if (totalLength > 0) {
          // 2px на точку для постепенного роста графика
          const dataWidth = totalLength * 2
          
          // Если график помещается в контейнер - используем данные
          // Если график больше контейнера - адаптируемся к ширине
          const finalWidth = dataWidth <= containerWidth ? dataWidth : containerWidth
          const shouldScroll = dataWidth > containerWidth
          
          console.log(`📊 Chart width calculation: totalLength=${totalLength}, dataWidth=${dataWidth}, containerWidth=${containerWidth}, finalWidth=${finalWidth}`)
          console.log(`📍 Chart positioning: ${dataWidth <= containerWidth ? 'growing' : 'adaptive'} mode, width=${finalWidth}px, scroll=${shouldScroll}`)
          setChartWidth(finalWidth)
          setNeedsScroll(shouldScroll)
        } else {
          // Якщо немає даних - повна ширина для плейсхолдера
          setChartWidth(containerWidth)
        }
      }
    }
    
    updateWidth()
    window.addEventListener('resize', updateWidth)
    return () => window.removeEventListener('resize', updateWidth)
  }, [externalChartData, forecastData, showForecast])

  useEffect(() => {
    const base = externalChartData || []
    const fc = showForecast ? (forecastData || []) : []
    if (base.length > 0 || fc.length > 0) {
      // Calculate Y-axis domain with padding only on top
      const allValues = [...base, ...fc].filter(v => Number.isFinite(v))
      const minValue = allValues.length > 0 ? Math.min(...allValues) : 0
      const maxValue = allValues.length > 0 ? Math.max(...allValues) : 1
      const range = maxValue - minValue
      
      // Add 25% padding on top (chart occupies 80% height)
      const paddingTop = range * 0.25
      
      // No bottom padding - chart sticks to bottom
      const yMin = Math.max(0, minValue)  // Use minimum value or 0, no padding
      const yMax = maxValue + paddingTop  // 25% padding top only
      setYAxisDomain([yMin, yMax])
    } else if (width) {
      setChartWidth(width)
    } else {
      setChartWidth(400) // Default fallback
    }
  }, [width, externalChartData, forecastData, showForecast])

  // Use external data if provided, otherwise show empty chart
  useEffect(() => {
    const base = externalChartData || []
    const fc = showForecast ? (forecastData || []) : []
    if (base.length === 0 && fc.length === 0) {
      setChartData([])
      return
    }
    
    // ПАРАЛЕЛЬНІ ГРАФІКИ: синій і помаранчевий незалежні, обидва починаються з X=0
    const merged: any[] = []
    
    // Знаходимо максимальну довжину для створення спільного часового ряду
    const maxLength = Math.max(base.length, fc.length)
    
    // Створюємо спільний масив, де кожна точка може містити обидва значення
    for (let i = 0; i < maxLength; i++) {
      const dataPoint: any = { time: i }
      
      // Додаємо історичні дані (синій графік) якщо вони є
      if (i < base.length && base[i] !== null && base[i] !== undefined && !isNaN(base[i])) {
        dataPoint.value = base[i]
      }
      
      // Додаємо прогноз (помаранчевий графік) якщо він є
      if (i < fc.length && fc[i] !== null && fc[i] !== undefined && !isNaN(fc[i])) {
        dataPoint.forecast = fc[i]
      }
      
      merged.push(dataPoint)
    }
    
    // ДІАГНОСТИКА: логуємо дані для тестової пари
    if (base.length > 0 || fc.length > 0) {
      console.log(`🧪 INDEPENDENT CHARTS: Blue (historical): ${base.length} points, Orange (forecast): ${fc.length} points, Max length: ${maxLength}`)
      if (base.length > 0) {
        console.log(`🧪 Blue chart (first 3):`, base.slice(0, 3))
        console.log(`🧪 Blue chart (last 3):`, base.slice(-3))
        console.log(`🧪 Blue chart range: ${Math.min(...base)} - ${Math.max(...base)}`)
      }
      if (fc.length > 0) {
        console.log(`🧪 Orange chart (first 3):`, fc.slice(0, 3))
      }
      console.log(`🧪 Merged data (first 3):`, merged.slice(0, 3))
      console.log(`🧪 Merged data (last 3):`, merged.slice(-3))
      
      // Проверяем, есть ли валидные данные в начале
      const firstValidIndex = merged.findIndex(point => point.value !== undefined && point.value !== null && !isNaN(point.value))
      console.log(`🧪 First valid data point at index: ${firstValidIndex}`)
    }
    
    setChartData(merged)
  }, [externalChartData, forecastData, showForecast])

  // Видалено генерацію випадкових даних - тепер тільки реальні дані з Backend

  // console.log(`Rendering chart with width: ${chartWidth}px, data points: ${chartData.length}`)
  
  // Якщо немає даних, показуємо плейсхолдер
  if (!chartData || chartData.length === 0) {
    return (
      <div 
        ref={containerRef}
        style={{
          width: '100%',
          height: `${height}px`, 
          display: 'flex', 
          alignItems: 'center', 
          justifyContent: 'center',
          color: '#9ca3af',
          fontSize: '14px',
          backgroundColor: 'transparent',
          borderRadius: '0',
          border: 'none'
        }}>
        No trade data
      </div>
    )
  }
  
  return (
    <div 
      ref={containerRef}
      style={{ 
        width: '100%',
        height: `${height}px`,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'flex-start',
        backgroundColor: 'transparent',
        borderRadius: '0',
        border: 'none',
        padding: 0,
        overflowX: needsScroll ? 'auto' : 'hidden',
        overflowY: 'hidden'
      }}>
      <div style={{
        width: `${chartWidth}px`,
        height: `${height}px`,
        flexShrink: 0,
        position: 'relative'
      }}>
        <AreaChart
          width={chartWidth}
          height={height}
          data={chartData}
          margin={{
            left: 0,
            right: 0,
            top: 3,
            bottom: 0,
          }}
        >
          <YAxis 
            domain={yAxisDomain}
            hide={true}
          />
          <XAxis 
            dataKey="time"
            type="number"
            scale="linear"
            domain={[0, 'dataMax']}
            tickCount={0}
            hide={true}
          />
          <defs>
            <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
              <stop
                offset="5%"
                stopColor={chartColor}
                stopOpacity={0.8}
              />
              <stop
                offset="95%"
                stopColor={chartColor}
                stopOpacity={0.1}
              />
            </linearGradient>
            <linearGradient id="fillForecast" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#f59e0b" stopOpacity={0.35} />
              <stop offset="95%" stopColor="#f59e0b" stopOpacity={0.05} />
            </linearGradient>
          </defs>
          <Area
            dataKey="value"
            type="natural"
            fill={`url(#${gradientId})`}
            fillOpacity={hasRealTrading === false ? 0.3 : 0.4}
            stroke={chartColor}
            strokeWidth={2}
            isAnimationActive={false}
          />
          {showForecast && (
            <Area
              dataKey="forecast"
              type="natural"
              fill="url(#fillForecast)"
              fillOpacity={0.25}
              stroke="#f59e0b"
              strokeWidth={2}
              isAnimationActive={false}
            />
          )}
          {/* Вертикальна лінія входу (зелена) */}
          {entrySec !== null && entrySec !== undefined && (
            <ReferenceLine
              x={entrySec}
              stroke="#f59e0b"
              strokeWidth={2}
              strokeDasharray="8 6"
            />
          )}
          {/* Вертикальна лінія виходу (червона) */}
          {exitSec !== null && exitSec !== undefined && (
            <ReferenceLine
              x={exitSec}
              stroke="#10b981"
              strokeWidth={2}
              strokeDasharray="8 6"
            />
          )}
          {/* Контрольні позначки для історичних переглядів */}
          {Array.isArray(historyMarkers) && historyMarkers
            .filter(marker => Number.isFinite(marker?.value))
            .map((marker, idx) => (
              <ReferenceLine
                key={`history-marker-${idx}-${marker.value}`}
                x={marker.value}
                stroke={marker.color || '#ef4444'}
                strokeWidth={2}
                strokeDasharray="8 6"
              />
            ))}
        </AreaChart>
      </div>
    </div>
  )
}

// Export with dynamic import to avoid hydration issues
const AreaChartComponentDynamic = dynamic<AreaChartComponentProps>(
  () => Promise.resolve(AreaChartComponent as React.ComponentType<AreaChartComponentProps>),
  {
    ssr: false,
    loading: () => <div style={{ width: '100%', height: '120px', backgroundColor: '#f3f4f6', borderRadius: '8px', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>Loading chart...</div>
  }
) as React.ComponentType<AreaChartComponentProps>

export { AreaChartComponentDynamic as AreaChartComponent }
