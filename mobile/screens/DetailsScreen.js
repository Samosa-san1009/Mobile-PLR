import React from 'react';
import {View, StyleSheet, TextInput, Button} from 'react-native';

import {useForm, Controller} from 'react-hook-form';

export function DetailsScreen({navigation}) {
  const {handleSubmit, control, reset} = useForm({
    defaultValues: {
      Name: 'Freyam',
      Age: '20',
      Sex: 'Male',
    },
  });

  async function onSubmit(data) {
    navigation.navigate('Config', {data});
  }

  return (
    <View style={styles.container}>
      <Controller
        control={control}
        render={({field: {onChange, onBlur, value}}) => (
          <View style={styles.DetailsInput}>
            <TextInput
              onBlur={onBlur}
              color="#292b2c"
              placeholder="Name"
              placeholderTextColor="#262626"
              onChangeText={value => onChange(value)}
              value={value}
            />
          </View>
        )}
        name="Name"
        rules={{required: true}}
      />
      <Controller
        control={control}
        render={({field: {onChange, onBlur, value}}) => (
          <View style={styles.DetailsInput}>
            <TextInput
              onBlur={onBlur}
              color="#292b2c"
              placeholder="Age"
              keyboardType="numeric"
              placeholderTextColor="#262626"
              onChangeText={value => onChange(value)}
              value={value}
            />
          </View>
        )}
        name="Age"
        rules={{required: true}}
      />
      <Controller
        control={control}
        render={({field: {onChange, onBlur, value}}) => (
          <View style={styles.DetailsInput}>
            <TextInput
              onBlur={onBlur}
              color="#292b2c"
              placeholder="Sex"
              placeholderTextColor="#262626"
              onChangeText={value => onChange(value)}
              value={value}
            />
          </View>
        )}
        name="Sex"
        rules={{required: true}}
      />

      <View style={{flex: 1, flexDirection: 'row'}}>
        <View style={{padding: 5, width: '25%'}}>
          <Button
            color="#A68192"
            title="Reset"
            onPress={() => {
              reset({
                Name: '',
                Age: '',
                Sex: '',
              });
            }}
          />
        </View>
        <View style={{padding: 5, width: '25%'}}>
          <Button
            color="#81A695"
            title="Proceed"
            onPress={handleSubmit(onSubmit)}
          />
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
    justifyContent: 'center',
    paddingTop: 50,
  },
  button: {
    backgroundColor: '#292b2c',
    borderRadius: 5,
    padding: 10,
    margin: 10,
  },
  DetailsInput: {
    width: '75%',
    borderWidth: 2,
    borderRadius: 10,
    margin: 10,
    padding: 5,
  },
});
