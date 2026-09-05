import React from 'react';
import { View } from 'react-native';
import { DomeMascot } from './DomeMascot';
import { mascotStateForStage, MascotContextOptions } from '../engine/catRuntime';
import type { RuntimeStage } from '../engine/lessonRuntime';
import type { MascotState } from '../mascot/mascotRegistry';
import { playExperience } from '../experience/experience';

export interface CatActivityLayerProps extends MascotContextOptions {
  stage: RuntimeStage;
  compact?: boolean;
  dragging?: boolean;
  onMascotPress?: () => void;
}

export function CatActivityLayer({
  stage,
  compact = false,
  dragging = false,
  explicitState,
  recording,
  speaking,
  hasError,
  hasHint,
  isComplete,
  isCorrect,
  onMascotPress,
}: CatActivityLayerProps) {
  const currentMascotState: MascotState = mascotStateForStage(stage, {
    explicitState,
    recording,
    speaking,
    hasError,
    hasHint,
    isComplete,
    isCorrect,
  });

  const mascotSize = compact ? 64 : 82;

  const handlePress = () => {
    playExperience('CAT_ACTION');
    onMascotPress?.();
  };

  return (
    <View
      testID="dome-cat-layer"
      accessibilityLabel={`Кот DOME: ${currentMascotState}`}
      style={{
        zIndex: 10,
        flexDirection: 'row',
        alignItems: 'center',
        justifyContent: 'center',
        overflow: 'visible',
        opacity: dragging ? 0.35 : 1,
        marginVertical: compact ? 2 : 4,
      }}
    >
      <DomeMascot
        state={currentMascotState}
        size={mascotSize}
        interactive={!dragging}
        onPress={handlePress}
        testID="dome-cat-touch"
      />
    </View>
  );
}
