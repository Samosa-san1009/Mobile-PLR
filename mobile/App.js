import React from 'react';

import {NavigationContainer} from '@react-navigation/native';
import {createNativeStackNavigator} from '@react-navigation/native-stack';

import {HomeScreen} from './screens/HomeScreen';
import {DetailsScreen} from './screens/DetailsScreen';
import {ConfigScreen} from './screens/ConfigScreen';
import {ExperimentScreen} from './screens/ExperimentScreen';
import {DashboardScreen} from './screens/DashboardScreen';
import {ResultsScreen} from './screens/ResultsScreen';

const Stack = createNativeStackNavigator();

export default function App() {
  return (
    <NavigationContainer>
      <Stack.Navigator initialRouteName="Home">
        <Stack.Screen
          name="Home"
          component={HomeScreen}
          options={{headerShown: false}}
        />
        <Stack.Screen
          name="Details"
          component={DetailsScreen}
          options={{
            title: 'Details',
            headerStyle: {backgroundColor: '#f7f7f7'},
            headerTitleAlign: 'center',
          }}
        />
        <Stack.Screen
          name="Config"
          component={ConfigScreen}
          options={{
            title: 'Config',
            headerStyle: {backgroundColor: '#f7f7f7'},
            headerTitleAlign: 'center',
          }}
        />
        <Stack.Screen
          name="Experiment"
          component={ExperimentScreen}
          options={{headerShown: false}}
        />
        <Stack.Screen
          name="Results"
          component={ResultsScreen}
          options={{headerShown: false}}
        />
        <Stack.Screen
          name="Dashboard"
          component={DashboardScreen}
          options={{
            title: 'Dashboard',
            headerStyle: {backgroundColor: '#f7f7f7'},
            headerTitleAlign: 'center',
          }}
        />
      </Stack.Navigator>
    </NavigationContainer>
  );
}
