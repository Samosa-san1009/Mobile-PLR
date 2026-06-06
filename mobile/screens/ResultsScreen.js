import React, {useEffect, useState} from 'react';
import {View, StyleSheet, ScrollView, Text, Dimensions} from 'react-native';
import {LineChart} from 'react-native-chart-kit';

import {cacheSession} from '../api';

const HEX_NAME = {
  '#FF0000': 'Red',
  '#00FF00': 'Green',
  '#0000FF': 'Blue',
  '#FFFF00': 'Yellow',
  '#FFFFFF': 'White',
};

export function ResultsScreen({navigation, route}) {
  const {experimentData} = route.params;
  const summary = experimentData.summary || {};
  const [cachedPath, setCachedPath] = useState(null);

  // Flatten {clip_path: result} into a sorted array
  const entries = Object.entries(summary).map(([clipPath, r]) => ({
    clipPath,
    hex:       r?._meta?.hex_color ?? '#000000',
    led:       r?._meta?.led_index ?? 0,
    baseline:  r?.baseline_diameter_mm,
    minimum:   r?.min_diameter_mm,
    amplitude: r?.constriction_amplitude_mm,
    latency:   r?.latency_ms,
  }));

  useEffect(() => {
    if (entries.length === 0) return;
    cacheSession(
      {
        name: experimentData.name,
        age: experimentData.age,
        sex: experimentData.sex,
        eye: experimentData.eye,
        color: experimentData.color,
        iterations: experimentData.iterations,
        duration: experimentData.duration,
        delay: experimentData.delay,
        intensity: experimentData.intensity,
        time: new Date().toISOString(),
      },
      summary,
    )
      .then(setCachedPath)
      .catch(e => console.warn('cacheSession failed', e));
  }, []);

  const chartLabels = entries.map((e, i) =>
    i % 2 === 0 ? HEX_NAME[e.hex] || e.hex.slice(1, 4) : '',
  );
  const baselineData  = entries.map(e => Number(e.baseline)  || 0);
  const minimumData   = entries.map(e => Number(e.minimum)   || 0);

  return (
    <ScrollView style={{backgroundColor: '#F7F7F7'}}>
      <Text style={styles.h1}>Session</Text>

      <Text style={styles.line}>Name: {experimentData.name}</Text>
      <Text style={styles.line}>Age: {experimentData.age}</Text>
      <Text style={styles.line}>Eye: {experimentData.eye}</Text>
      <Text style={styles.line}>Color: {experimentData.color}</Text>
      <Text style={styles.line}>Iterations: {experimentData.iterations}</Text>
      <Text style={styles.line}>Duration: {experimentData.duration}s</Text>
      <Text style={styles.line}>Delay: {experimentData.delay}s</Text>
      <Text style={styles.line}>Intensity: {experimentData.intensity}%</Text>

      <Text style={styles.h1}>Per-flash results</Text>

      {entries.length === 0 && (
        <Text style={styles.line}>No results returned.</Text>
      )}

      {entries.map((e, i) => (
        <View key={i} style={styles.card}>
          <Text style={styles.cardTitle}>
            LED {e.led}  ·  {HEX_NAME[e.hex] || e.hex}
          </Text>
          <Text style={styles.cardLine}>
            Baseline: {e.baseline} mm   Min: {e.minimum} mm
          </Text>
          <Text style={styles.cardLine}>
            Amplitude: {e.amplitude} mm   Latency: {e.latency} ms
          </Text>
        </View>
      ))}

      {entries.length > 1 && (
        <View style={{marginHorizontal: 20, marginVertical: 20}}>
          <LineChart
            data={{
              labels: chartLabels,
              datasets: [
                {data: baselineData, color: () => 'rgba(40,90,200,1)'},
                {data: minimumData,  color: () => 'rgba(200,60,60,1)'},
              ],
              legend: ['baseline mm', 'min mm'],
            }}
            width={Dimensions.get('window').width - 40}
            height={220}
            yAxisSuffix=" mm"
            chartConfig={{
              backgroundGradientFrom: '#ffffff',
              backgroundGradientTo:   '#ffffff',
              decimalPlaces: 2,
              color:      (o = 1) => `rgba(0,0,0,${o})`,
              labelColor: (o = 1) => `rgba(0,0,0,${o})`,
              propsForDots: {r: '3'},
            }}
            bezier
            style={{borderRadius: 16}}
          />
        </View>
      )}

      {cachedPath && (
        <Text style={styles.cached}>Saved to {cachedPath}</Text>
      )}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  h1:   {fontSize: 20, fontWeight: 'bold', color: 'black',
         textAlign: 'center', marginTop: 24},
  line: {fontSize: 15, color: 'black', textAlign: 'center', marginTop: 10},
  card: {marginHorizontal: 20, marginTop: 12, padding: 12,
         backgroundColor: '#ffffff', borderRadius: 10,
         shadowColor: '#000', shadowOpacity: 0.05, shadowRadius: 4},
  cardTitle: {fontSize: 16, fontWeight: 'bold', color: 'black',
              marginBottom: 6},
  cardLine: {fontSize: 14, color: '#333', marginTop: 2},
  cached: {fontSize: 12, color: '#666', textAlign: 'center', marginVertical: 16},
});
