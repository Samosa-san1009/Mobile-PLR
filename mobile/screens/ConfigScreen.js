import React, {useState} from 'react';
import {View, StyleSheet, Text, Button, TouchableOpacity} from 'react-native';
import Slider from '@react-native-community/slider';
import ColorPalette from 'react-native-color-palette';
import {exp} from 'react-native/Libraries/Animated/Easing';
import {startSession} from '../api';

export function ConfigScreen({navigation, route}) {
  const {data} = route.params;

  const [eye, setEye] = useState('Left');
  const [color, setColor] = useState('#F7F7F7');
  const [iterations, setIterations] = useState(0.0);
  const [duration, setDuration] = useState(0.0);
  const [delay, setDelay] = useState(0.0);
  const [intensity, setIntensity] = useState(0.0);

  const colorID = {
    '#EB5353': 'Red',
    '#36AE7C': 'Green',
    '#187498': 'Blue',
    '#F9D923': 'Yellow',
    '#F7F7F7': 'White',
    '#080808': 'All',
  };

  const experimentData = {
    name: data.Name,
    age: data.Age,
    sex: data.Sex,
    time: '',
    eye: eye,
    color: color,
    iterations: iterations,
    duration: duration,
    delay: delay,
    intensity: intensity,
    pupilDiameters: '',
  };

  async function startExperiment() {
    const payload = {
      participant: {name: data.Name, age: data.Age, sex: data.Sex},
      eye, color, iterations, duration, delay, intensity,
    };

    try {
      await startSession(payload);
    } catch (e) {
      console.warn('startSession failed', e);
      return;
    }

    navigation.navigate('Experiment', {experimentData});
  }

  return (
    <View style={styles.container}>
      <View>
        <Text style={styles.details}>Eye: {eye}</Text>
        <View style={styles.buttonContainer}>
          <TouchableOpacity
            style={{
              backgroundColor: eye === 'Left' ? '#8192A6' : 'transparent',
              borderRadius: 30,
              height: 60,
              width: 60,
            }}
            onPress={() => {
              setEye('Left');
            }}>
            <View style={styles.button}>
              <Text style={styles.eyeText}>L</Text>
            </View>
          </TouchableOpacity>
          <TouchableOpacity
            style={{
              backgroundColor: eye === 'Right' ? '#8192A6' : 'transparent',
              borderRadius: 30,
              height: 60,
              width: 60,
            }}
            onPress={() => {
              setEye('Right');
            }}>
            <View style={styles.button}>
              <Text style={styles.eyeText}>R</Text>
            </View>
          </TouchableOpacity>
        </View>

        <Text style={styles.details}>Color: {color}</Text>
        <ColorPalette
          onChange={color => {
            setColor(colorID[color]);
          }}
          defaultColor={'#080808'}
          colors={[
            '#EB5353',
            '#36AE7C',
            '#187498',
            '#F9D923',
            '#F7F7F7',
            '#080808',
          ]}
          title={''}
          icon={
            <View
              style={{
                backgroundColor: 'transparent',
                borderRadius: 50,
                width: 30,
                height: 30,
                justifyContent: 'center',
                alignItems: 'center',
              }}>
              <View
                style={{
                  backgroundColor: '#080808',
                  borderRadius: 50,
                  width: 14,
                  height: 14,
                }}
              />
            </View>
          }
        />

        <Text style={styles.details}>Iterations: {iterations}</Text>
        <Slider
          step={1}
          minimumValue={0}
          maximumValue={10}
          value={iterations}
          onValueChange={slideValue => setIterations(slideValue)}
          minimumTrackTintColor="#A9A9A9"
          maximumTrackTintColor="#d3d3d3"
          thumbTintColor="#474747"
        />

        <Text style={styles.details}>Duration: {duration}s</Text>
        <Slider
          step={1}
          minimumValue={0}
          maximumValue={10}
          value={duration}
          onValueChange={slideValue => setDuration(slideValue)}
          minimumTrackTintColor="#A9A9A9"
          maximumTrackTintColor="#d3d3d3"
          thumbTintColor="#474747"
        />

        <Text style={styles.details}>Delay: {delay}s</Text>
        <Slider
          step={1}
          minimumValue={0}
          maximumValue={10}
          value={delay}
          onValueChange={slideValue => setDelay(slideValue)}
          minimumTrackTintColor="#A9A9A9"
          maximumTrackTintColor="#d3d3d3"
          thumbTintColor="#474747"
        />

        <Text style={styles.details}>Intensity: {intensity}%</Text>
        <Slider
          step={5}
          minimumValue={0}
          maximumValue={100}
          value={intensity}
          onValueChange={slideValue => setIntensity(slideValue)}
          minimumTrackTintColor="#A9A9A9"
          maximumTrackTintColor="#d3d3d3"
          thumbTintColor="#474747"
        />
      </View>

      <View
        style={{
          marginTop: 80,
          flexDirection: 'row',
        }}>
        <View style={{padding: 5, width: '55%'}}>
          <Button
            color="#81A695"
            title="Start Experiment"
            onPress={() => {
              startExperiment(experimentData);
            }}></Button>
        </View>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#F7F7F7',
    alignItems: 'center',
    paddingTop: 30,
  },
  buttonContainer: {
    flexDirection: 'row',
    justifyContent: 'space-between',
  },
  button: {
    borderColor: 'black',
    borderWidth: 2,
    height: 60,
    width: 60,
    borderRadius: 30,
  },
  details: {
    fontSize: 20,
    fontWeight: 'bold',
    marginTop: 10,
    paddingTop: 10,
    textAlign: 'center',
    color: '#000000',
    marginBottom: 5,
  },
  eyeText: {
    fontSize: 30,
    textAlign: 'center',
    padding: 10,
    color: '#000000',
  },
  detailsInput: {
    width: '75%',
    borderWidth: 2,
    borderRadius: 10,
    margin: 10,
    padding: 5,
  },
});
