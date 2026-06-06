import React, {useEffect, useRef} from 'react';
import {View, StyleSheet} from 'react-native';
import Video from 'react-native-video';

import EyezerScenery from '../assets/videos/app_bg_loop.mp4';
import {getStatus, getResults, STAGE} from '../api';

export function ExperimentScreen({navigation, route}) {
  const {experimentData} = route.params;
  const cancelled = useRef(false);

  useEffect(() => {
    cancelled.current = false;
    let timer;

    async function poll() {
      if (cancelled.current) return;

      let stage = STAGE.RECORDING;
      try {
        const s = await getStatus();
        stage = s.stage;
      } catch (e) {
        // The Pi shuts down its HTTP listener once it starts running the
        // model (so the cooler-less Pi 4 can spend full CPU on inference).
        // A fetch failure here usually means we're in that gap — keep polling
        // /results instead.
        stage = STAGE.INFERENCE;
      }

      if (stage === STAGE.DONE || stage === STAGE.INFERENCE) {
        try {
          const summary = await getResults();
          experimentData.summary = summary;
          experimentData.pupilDiameters = '';   // legacy field, no longer used
          navigation.navigate('Results', {experimentData});
          return;
        } catch (e) {
          // results not ready yet — keep polling
        }
      }

      if (stage === STAGE.ERROR) {
        console.warn('Pi reported error stage');
        navigation.goBack();
        return;
      }

      timer = setTimeout(poll, 1500);
    }

    poll();
    return () => {
      cancelled.current = true;
      if (timer) clearTimeout(timer);
    };
  }, [experimentData, navigation]);

  return (
    <View style={styles.container}>
      <Video
        repeat
        source={EyezerScenery}
        resizeMode="cover"
        style={styles.backgroundVideo}
      />
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
  backgroundVideo: {
    position: 'absolute',
    top: 0,
    left: 0,
    bottom: 0,
    right: 0,
  },
});
