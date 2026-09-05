import React, { useEffect, useRef, useState } from 'react';
import {
  Animated,
  Easing,
  Image,
  Pressable,
  StyleProp,
  StyleSheet,
  View,
  ViewStyle,
} from 'react-native';
import {
  getMascotAsset,
  getMascotMeta,
  MascotAnimationType,
  MascotState,
} from '../mascot/mascotRegistry';

export interface DomeMascotProps {
  /**
   * The emotional/behavioral state of the mascot.
   * Default: 'HELLO'
   */
  state?: MascotState | string | null;

  /**
   * Visual width of the mascot container.
   * Height is proportional (size * 1.15) to maintain stable layout.
   * Default: 80
   */
  size?: number;

  /**
   * Whether tapping the mascot triggers fun micro-reactions.
   * Default: true
   */
  interactive?: boolean;

  /**
   * Optional custom tap callback.
   */
  onPress?: () => void;

  /**
   * Optional container style.
   */
  style?: StyleProp<ViewStyle>;

  /**
   * Test ID for automated QA / integration tests.
   */
  testID?: string;
}

type TapReactionType = 'JUMP' | 'WIGGLE' | 'POP' | 'WOBBLE';

export function DomeMascot({
  state = 'HELLO',
  size = 80,
  interactive = true,
  onPress,
  style,
  testID = 'dome-mascot',
}: DomeMascotProps) {
  const currentState = (state as MascotState) || 'HELLO';
  const meta = getMascotMeta(currentState);

  // Layout bounds (strictly locked to eliminate layout jump between state changes)
  const containerWidth = size;
  const containerHeight = Math.round(size * 1.12);

  // State asset transition
  const [displayedState, setDisplayedState] = useState<MascotState>(currentState);
  const stateScaleAnim = useRef(new Animated.Value(1)).current;

  // Persistent continuous animation values
  const loopTranslateY = useRef(new Animated.Value(0)).current;
  const loopTranslateX = useRef(new Animated.Value(0)).current;
  const loopScale = useRef(new Animated.Value(1)).current;
  const loopScaleX = useRef(new Animated.Value(1)).current;
  const loopScaleY = useRef(new Animated.Value(1)).current;
  const loopRotate = useRef(new Animated.Value(0)).current;

  // Natural idle micro-animations (breathing, blink, head tilt)
  const idleTranslateY = useRef(new Animated.Value(0)).current;
  const idleScaleX = useRef(new Animated.Value(1)).current;
  const idleScaleY = useRef(new Animated.Value(1)).current;
  const blinkScaleY = useRef(new Animated.Value(1)).current;
  const microHeadTilt = useRef(new Animated.Value(0)).current;

  // Tap reaction animation values
  const tapTranslateY = useRef(new Animated.Value(0)).current;
  const tapTranslateX = useRef(new Animated.Value(0)).current;
  const tapScale = useRef(new Animated.Value(1)).current;
  const tapScaleX = useRef(new Animated.Value(1)).current;
  const tapScaleY = useRef(new Animated.Value(1)).current;
  const tapRotate = useRef(new Animated.Value(0)).current;

  const activeTapAnimRef = useRef<Animated.CompositeAnimation | null>(null);
  const isTappingRef = useRef(false);
  const tapCountRef = useRef(0);
  const lastTapTimeRef = useRef(0);
  const tapResetTimerRef = useRef<any>(null);

  // 1. Lively, fail-safe state transition: immediately updates asset and plays a subtle bouncy pop
  useEffect(() => {
    if (currentState !== displayedState) {
      setDisplayedState(currentState);
      stateScaleAnim.setValue(0.92);
      Animated.spring(stateScaleAnim, {
        toValue: 1,
        speed: 26,
        bounciness: 7,
        useNativeDriver: true,
      }).start();
    }
  }, [currentState, displayedState, stateScaleAnim]);

  // 1b. Organic idle micro-animations: gentle chest/belly breathing, natural eye blink, listening head tilt
  useEffect(() => {
    idleTranslateY.setValue(0);
    idleScaleX.setValue(1);
    idleScaleY.setValue(1);
    blinkScaleY.setValue(1);
    microHeadTilt.setValue(0);

    // Continuous soft rhythmic breathing
    const breatheAnim = Animated.loop(
      Animated.sequence([
        Animated.parallel([
          Animated.timing(idleTranslateY, {
            toValue: -1.5,
            duration: 1600,
            easing: Easing.inOut(Easing.sin),
            useNativeDriver: true,
          }),
          Animated.timing(idleScaleY, {
            toValue: 1.022,
            duration: 1600,
            easing: Easing.inOut(Easing.sin),
            useNativeDriver: true,
          }),
          Animated.timing(idleScaleX, {
            toValue: 0.988,
            duration: 1600,
            easing: Easing.inOut(Easing.sin),
            useNativeDriver: true,
          }),
        ]),
        Animated.parallel([
          Animated.timing(idleTranslateY, {
            toValue: 0,
            duration: 1800,
            easing: Easing.inOut(Easing.sin),
            useNativeDriver: true,
          }),
          Animated.timing(idleScaleY, {
            toValue: 0.985,
            duration: 1800,
            easing: Easing.inOut(Easing.sin),
            useNativeDriver: true,
          }),
          Animated.timing(idleScaleX, {
            toValue: 1.012,
            duration: 1800,
            easing: Easing.inOut(Easing.sin),
            useNativeDriver: true,
          }),
        ]),
      ])
    );

    // Natural occasional cartoon blink (quick eyelid drop and open)
    const blinkAnim = Animated.loop(
      Animated.sequence([
        Animated.delay(3400),
        Animated.timing(blinkScaleY, {
          toValue: 0.94,
          duration: 65,
          easing: Easing.out(Easing.ease),
          useNativeDriver: true,
        }),
        Animated.timing(blinkScaleY, {
          toValue: 1,
          duration: 90,
          easing: Easing.in(Easing.ease),
          useNativeDriver: true,
        }),
        Animated.delay(2200),
      ])
    );

    // Inquisitive head tilt (observing the child and lesson)
    const tiltAnim = Animated.loop(
      Animated.sequence([
        Animated.delay(4200),
        Animated.timing(microHeadTilt, {
          toValue: 0.5,
          duration: 380,
          easing: Easing.inOut(Easing.ease),
          useNativeDriver: true,
        }),
        Animated.delay(1400),
        Animated.timing(microHeadTilt, {
          toValue: -0.35,
          duration: 450,
          easing: Easing.inOut(Easing.ease),
          useNativeDriver: true,
        }),
        Animated.delay(800),
        Animated.timing(microHeadTilt, {
          toValue: 0,
          duration: 360,
          easing: Easing.inOut(Easing.ease),
          useNativeDriver: true,
        }),
        Animated.delay(3200),
      ])
    );

    breatheAnim.start();
    blinkAnim.start();
    tiltAnim.start();

    return () => {
      breatheAnim.stop();
      blinkAnim.stop();
      tiltAnim.stop();
    };
  }, [idleTranslateY, idleScaleX, idleScaleY, blinkScaleY, microHeadTilt]);

  // 2. Programmatic cyclic loop tailored to each emotion
  useEffect(() => {
    // Reset base transform values before starting animation
    loopTranslateY.setValue(0);
    loopTranslateX.setValue(0);
    loopScale.setValue(1);
    loopScaleX.setValue(1);
    loopScaleY.setValue(1);
    loopRotate.setValue(0);

    let activeAnim: Animated.CompositeAnimation | null = null;
    const animType: MascotAnimationType = meta.animationType;

    switch (animType) {
      case 'bounce':
        // Energetic squash-and-stretch bounce for LETS_GO, IDEA, GREAT
        activeAnim = Animated.loop(
          Animated.sequence([
            Animated.parallel([
              Animated.timing(loopTranslateY, {
                toValue: -8,
                duration: 260,
                easing: Easing.out(Easing.quad),
                useNativeDriver: true,
              }),
              Animated.timing(loopScaleY, {
                toValue: 1.05,
                duration: 260,
                easing: Easing.out(Easing.quad),
                useNativeDriver: true,
              }),
              Animated.timing(loopScaleX, {
                toValue: 0.96,
                duration: 260,
                easing: Easing.out(Easing.quad),
                useNativeDriver: true,
              }),
            ]),
            Animated.parallel([
              Animated.timing(loopTranslateY, {
                toValue: 0,
                duration: 240,
                easing: Easing.in(Easing.quad),
                useNativeDriver: true,
              }),
              Animated.timing(loopScaleY, {
                toValue: 0.94,
                duration: 240,
                easing: Easing.in(Easing.quad),
                useNativeDriver: true,
              }),
              Animated.timing(loopScaleX, {
                toValue: 1.05,
                duration: 240,
                easing: Easing.in(Easing.quad),
                useNativeDriver: true,
              }),
            ]),
            Animated.parallel([
              Animated.timing(loopScaleY, {
                toValue: 1,
                duration: 140,
                easing: Easing.ease,
                useNativeDriver: true,
              }),
              Animated.timing(loopScaleX, {
                toValue: 1,
                duration: 140,
                easing: Easing.ease,
                useNativeDriver: true,
              }),
            ]),
            Animated.delay(1200),
          ])
        );
        break;

      case 'wave':
        // Gentle wave tilt for HELLO, WAVE
        activeAnim = Animated.loop(
          Animated.sequence([
            Animated.timing(loopRotate, {
              toValue: 1,
              duration: 400,
              easing: Easing.inOut(Easing.sin),
              useNativeDriver: true,
            }),
            Animated.timing(loopRotate, {
              toValue: -1,
              duration: 750,
              easing: Easing.inOut(Easing.sin),
              useNativeDriver: true,
            }),
            Animated.timing(loopRotate, {
              toValue: 0,
              duration: 450,
              easing: Easing.inOut(Easing.sin),
              useNativeDriver: true,
            }),
            Animated.delay(1800),
          ])
        );
        break;

      case 'float':
        // Subtle floating bob for THINKING
        activeAnim = Animated.loop(
          Animated.sequence([
            Animated.timing(loopTranslateY, {
              toValue: -5,
              duration: 1200,
              easing: Easing.inOut(Easing.sin),
              useNativeDriver: true,
            }),
            Animated.timing(loopTranslateY, {
              toValue: 3,
              duration: 1200,
              easing: Easing.inOut(Easing.sin),
              useNativeDriver: true,
            }),
          ])
        );
        break;

      case 'pulse':
        // Gentle attentive listening pulse for LISTENING
        activeAnim = Animated.loop(
          Animated.sequence([
            Animated.timing(loopScale, {
              toValue: 1.04,
              duration: 700,
              easing: Easing.inOut(Easing.quad),
              useNativeDriver: true,
            }),
            Animated.timing(loopScale, {
              toValue: 1,
              duration: 700,
              easing: Easing.inOut(Easing.quad),
              useNativeDriver: true,
            }),
          ])
        );
        break;

      case 'celebrate':
        // Springy double jump for CELEBRATE
        activeAnim = Animated.loop(
          Animated.sequence([
            Animated.timing(loopTranslateY, {
              toValue: -12,
              duration: 260,
              easing: Easing.out(Easing.back(1.5)),
              useNativeDriver: true,
            }),
            Animated.timing(loopTranslateY, {
              toValue: 0,
              duration: 220,
              easing: Easing.in(Easing.quad),
              useNativeDriver: true,
            }),
            Animated.timing(loopTranslateY, {
              toValue: -6,
              duration: 180,
              easing: Easing.out(Easing.quad),
              useNativeDriver: true,
            }),
            Animated.timing(loopTranslateY, {
              toValue: 0,
              duration: 180,
              easing: Easing.in(Easing.quad),
              useNativeDriver: true,
            }),
            Animated.delay(1400),
          ])
        );
        break;

      case 'breathing':
        // Very slow, calm squash & stretch breathing for SLEEPING
        activeAnim = Animated.loop(
          Animated.sequence([
            Animated.parallel([
              Animated.timing(loopScaleY, {
                toValue: 1.035,
                duration: 1800,
                easing: Easing.inOut(Easing.sin),
                useNativeDriver: true,
              }),
              Animated.timing(loopScaleX, {
                toValue: 0.97,
                duration: 1800,
                easing: Easing.inOut(Easing.sin),
                useNativeDriver: true,
              }),
            ]),
            Animated.parallel([
              Animated.timing(loopScaleY, {
                toValue: 0.975,
                duration: 1800,
                easing: Easing.inOut(Easing.sin),
                useNativeDriver: true,
              }),
              Animated.timing(loopScaleX, {
                toValue: 1.025,
                duration: 1800,
                easing: Easing.inOut(Easing.sin),
                useNativeDriver: true,
              }),
            ]),
          ])
        );
        break;

      case 'tilt':
        // Soft shrugging head tilt for CONFUSED
        activeAnim = Animated.loop(
          Animated.sequence([
            Animated.timing(loopRotate, {
              toValue: 1.2,
              duration: 450,
              easing: Easing.inOut(Easing.ease),
              useNativeDriver: true,
            }),
            Animated.timing(loopRotate, {
              toValue: -1.2,
              duration: 900,
              easing: Easing.inOut(Easing.ease),
              useNativeDriver: true,
            }),
            Animated.timing(loopRotate, {
              toValue: 0,
              duration: 450,
              easing: Easing.inOut(Easing.ease),
              useNativeDriver: true,
            }),
            Animated.delay(1200),
          ])
        );
        break;

      case 'applause':
        // Lively rhythmic applause bounce
        activeAnim = Animated.loop(
          Animated.sequence([
            Animated.timing(loopTranslateY, {
              toValue: -5,
              duration: 160,
              useNativeDriver: true,
            }),
            Animated.timing(loopTranslateY, {
              toValue: 0,
              duration: 160,
              useNativeDriver: true,
            }),
            Animated.timing(loopTranslateY, {
              toValue: -5,
              duration: 160,
              useNativeDriver: true,
            }),
            Animated.timing(loopTranslateY, {
              toValue: 0,
              duration: 160,
              useNativeDriver: true,
            }),
            Animated.delay(1000),
          ])
        );
        break;

      case 'heartbeat':
        // Lub-dub pulsing scale for LOVE
        activeAnim = Animated.loop(
          Animated.sequence([
            Animated.timing(loopScale, {
              toValue: 1.06,
              duration: 160,
              easing: Easing.out(Easing.ease),
              useNativeDriver: true,
            }),
            Animated.timing(loopScale, {
              toValue: 1.0,
              duration: 180,
              easing: Easing.in(Easing.ease),
              useNativeDriver: true,
            }),
            Animated.timing(loopScale, {
              toValue: 1.04,
              duration: 140,
              easing: Easing.out(Easing.ease),
              useNativeDriver: true,
            }),
            Animated.timing(loopScale, {
              toValue: 1.0,
              duration: 220,
              easing: Easing.in(Easing.ease),
              useNativeDriver: true,
            }),
            Animated.delay(1200),
          ])
        );
        break;

      case 'sad_droop':
        // Gentle downward sigh for SAD
        activeAnim = Animated.loop(
          Animated.sequence([
            Animated.timing(loopTranslateY, {
              toValue: 4,
              duration: 1600,
              easing: Easing.inOut(Easing.sin),
              useNativeDriver: true,
            }),
            Animated.timing(loopTranslateY, {
              toValue: 0,
              duration: 1600,
              easing: Easing.inOut(Easing.sin),
              useNativeDriver: true,
            }),
            Animated.delay(600),
          ])
        );
        break;

      case 'wiggle':
      default:
        // Playful wiggle + micro squash-and-stretch breathing
        activeAnim = Animated.loop(
          Animated.sequence([
            Animated.parallel([
              Animated.timing(loopRotate, {
                toValue: 0.8,
                duration: 250,
                useNativeDriver: true,
              }),
              Animated.timing(loopScaleY, {
                toValue: 1.02,
                duration: 250,
                useNativeDriver: true,
              }),
              Animated.timing(loopScaleX, {
                toValue: 0.985,
                duration: 250,
                useNativeDriver: true,
              }),
            ]),
            Animated.parallel([
              Animated.timing(loopRotate, {
                toValue: -0.8,
                duration: 500,
                useNativeDriver: true,
              }),
              Animated.timing(loopScaleY, {
                toValue: 0.985,
                duration: 500,
                useNativeDriver: true,
              }),
              Animated.timing(loopScaleX, {
                toValue: 1.015,
                duration: 500,
                useNativeDriver: true,
              }),
            ]),
            Animated.parallel([
              Animated.timing(loopRotate, {
                toValue: 0,
                duration: 250,
                useNativeDriver: true,
              }),
              Animated.timing(loopScaleY, {
                toValue: 1,
                duration: 250,
                useNativeDriver: true,
              }),
              Animated.timing(loopScaleX, {
                toValue: 1,
                duration: 250,
                useNativeDriver: true,
              }),
            ]),
            Animated.delay(1500),
          ])
        );
        break;
    }

    activeAnim?.start();

    return () => {
      activeAnim?.stop();
    };
  }, [meta.animationType, loopTranslateY, loopTranslateX, loopScale, loopScaleX, loopScaleY, loopRotate]);

  // 3. Interactive tap handler (single tap + rapid combo multi-taps, zero transform accumulation)
  const handlePress = () => {
    if (!interactive) return;
    onPress?.();

    // Stop previous tap animation if still running
    if (activeTapAnimRef.current) {
      activeTapAnimRef.current.stop();
      activeTapAnimRef.current = null;
    }

    // Strictly reset tap transform values to baseline to prevent any drift/accumulation
    tapTranslateY.setValue(0);
    tapTranslateX.setValue(0);
    tapScale.setValue(1);
    tapScaleX.setValue(1);
    tapScaleY.setValue(1);
    tapRotate.setValue(0);

    const now = Date.now();
    if (now - lastTapTimeRef.current < 650) {
      tapCountRef.current += 1;
    } else {
      tapCountRef.current = 1;
    }
    lastTapTimeRef.current = now;

    if (tapResetTimerRef.current) clearTimeout(tapResetTimerRef.current);
    tapResetTimerRef.current = setTimeout(() => {
      tapCountRef.current = 0;
    }, 750);

    let tapAnim: Animated.CompositeAnimation;

    if (tapCountRef.current >= 3) {
      // Rapid 3+ taps: MEGA joy rocket jump & spin
      tapAnim = Animated.sequence([
        // Deep anticipation squash
        Animated.parallel([
          Animated.timing(tapScaleY, { toValue: 0.76, duration: 60, useNativeDriver: true }),
          Animated.timing(tapScaleX, { toValue: 1.24, duration: 60, useNativeDriver: true }),
          Animated.timing(tapTranslateY, { toValue: 7, duration: 60, useNativeDriver: true }),
        ]),
        // Rocket spring launch
        Animated.parallel([
          Animated.timing(tapTranslateY, {
            toValue: -32,
            duration: 210,
            easing: Easing.out(Easing.back(1.6)),
            useNativeDriver: true,
          }),
          Animated.timing(tapScaleY, { toValue: 1.24, duration: 210, useNativeDriver: true }),
          Animated.timing(tapScaleX, { toValue: 0.86, duration: 210, useNativeDriver: true }),
          Animated.sequence([
            Animated.timing(tapRotate, { toValue: 2.0, duration: 80, useNativeDriver: true }),
            Animated.timing(tapRotate, { toValue: -2.0, duration: 80, useNativeDriver: true }),
            Animated.timing(tapRotate, { toValue: 0, duration: 50, useNativeDriver: true }),
          ]),
        ]),
        // Cushion landing squash
        Animated.parallel([
          Animated.timing(tapTranslateY, {
            toValue: 2,
            duration: 130,
            easing: Easing.in(Easing.quad),
            useNativeDriver: true,
          }),
          Animated.timing(tapScaleY, { toValue: 0.86, duration: 110, useNativeDriver: true }),
          Animated.timing(tapScaleX, { toValue: 1.14, duration: 110, useNativeDriver: true }),
        ]),
        // Settle spring back
        Animated.parallel([
          Animated.spring(tapScaleY, { toValue: 1, friction: 4.5, tension: 45, useNativeDriver: true }),
          Animated.spring(tapScaleX, { toValue: 1, friction: 4.5, tension: 45, useNativeDriver: true }),
          Animated.spring(tapTranslateY, { toValue: 0, friction: 5, tension: 45, useNativeDriver: true }),
        ]),
      ]);
    } else if (tapCountRef.current === 2) {
      // Rapid 2 taps: double spring leap
      tapAnim = Animated.sequence([
        Animated.parallel([
          Animated.timing(tapScaleY, { toValue: 0.82, duration: 65, useNativeDriver: true }),
          Animated.timing(tapScaleX, { toValue: 1.18, duration: 65, useNativeDriver: true }),
          Animated.timing(tapTranslateY, { toValue: 5, duration: 65, useNativeDriver: true }),
        ]),
        Animated.parallel([
          Animated.timing(tapTranslateY, {
            toValue: -22,
            duration: 180,
            easing: Easing.out(Easing.back(1.5)),
            useNativeDriver: true,
          }),
          Animated.timing(tapScaleY, { toValue: 1.16, duration: 180, useNativeDriver: true }),
          Animated.timing(tapScaleX, { toValue: 0.90, duration: 180, useNativeDriver: true }),
        ]),
        Animated.parallel([
          Animated.timing(tapTranslateY, { toValue: 0, duration: 120, useNativeDriver: true }),
          Animated.timing(tapScaleY, { toValue: 0.90, duration: 100, useNativeDriver: true }),
          Animated.timing(tapScaleX, { toValue: 1.10, duration: 100, useNativeDriver: true }),
        ]),
        Animated.parallel([
          Animated.spring(tapScaleY, { toValue: 1, friction: 4, tension: 45, useNativeDriver: true }),
          Animated.spring(tapScaleX, { toValue: 1, friction: 4, tension: 45, useNativeDriver: true }),
          Animated.spring(tapTranslateY, { toValue: 0, friction: 4, tension: 45, useNativeDriver: true }),
        ]),
      ]);
    } else {
      // Single tap: varied fun micro-reactions (JUMP, WIGGLE, POP, HOP, WOBBLE)
      const reactions = ['JUMP', 'WIGGLE', 'POP', 'HOP', 'WOBBLE'] as const;
      const chosen = reactions[Math.floor(Math.random() * reactions.length)];

      switch (chosen) {
        case 'JUMP':
          tapAnim = Animated.sequence([
            Animated.parallel([
              Animated.timing(tapScaleY, { toValue: 0.86, duration: 65, useNativeDriver: true }),
              Animated.timing(tapScaleX, { toValue: 1.14, duration: 65, useNativeDriver: true }),
            ]),
            Animated.parallel([
              Animated.timing(tapTranslateY, {
                toValue: -17,
                duration: 160,
                easing: Easing.out(Easing.quad),
                useNativeDriver: true,
              }),
              Animated.timing(tapScaleY, { toValue: 1.12, duration: 160, useNativeDriver: true }),
              Animated.timing(tapScaleX, { toValue: 0.92, duration: 160, useNativeDriver: true }),
            ]),
            Animated.parallel([
              Animated.spring(tapTranslateY, { toValue: 0, friction: 4, tension: 50, useNativeDriver: true }),
              Animated.spring(tapScaleY, { toValue: 1, friction: 4, tension: 45, useNativeDriver: true }),
              Animated.spring(tapScaleX, { toValue: 1, friction: 4, tension: 45, useNativeDriver: true }),
            ]),
          ]);
          break;

        case 'WIGGLE':
          tapAnim = Animated.sequence([
            Animated.timing(tapRotate, { toValue: 1.2, duration: 60, useNativeDriver: true }),
            Animated.timing(tapRotate, { toValue: -1.2, duration: 90, useNativeDriver: true }),
            Animated.timing(tapRotate, { toValue: 0.8, duration: 80, useNativeDriver: true }),
            Animated.timing(tapRotate, { toValue: -0.8, duration: 80, useNativeDriver: true }),
            Animated.timing(tapRotate, { toValue: 0, duration: 70, useNativeDriver: true }),
          ]);
          break;

        case 'POP':
          tapAnim = Animated.sequence([
            Animated.parallel([
              Animated.timing(tapScale, {
                toValue: 1.15,
                duration: 110,
                easing: Easing.out(Easing.ease),
                useNativeDriver: true,
              }),
              Animated.timing(tapTranslateY, { toValue: -5, duration: 110, useNativeDriver: true }),
            ]),
            Animated.parallel([
              Animated.spring(tapScale, { toValue: 1, friction: 3.5, tension: 45, useNativeDriver: true }),
              Animated.spring(tapTranslateY, { toValue: 0, friction: 4, tension: 45, useNativeDriver: true }),
            ]),
          ]);
          break;

        case 'HOP':
          tapAnim = Animated.sequence([
            Animated.timing(tapTranslateY, { toValue: -8, duration: 90, useNativeDriver: true }),
            Animated.timing(tapTranslateY, { toValue: 0, duration: 90, useNativeDriver: true }),
            Animated.timing(tapTranslateY, { toValue: -13, duration: 110, useNativeDriver: true }),
            Animated.spring(tapTranslateY, { toValue: 0, friction: 4, tension: 50, useNativeDriver: true }),
          ]);
          break;

        case 'WOBBLE':
        default:
          tapAnim = Animated.sequence([
            Animated.parallel([
              Animated.timing(tapTranslateX, { toValue: -6, duration: 65, useNativeDriver: true }),
              Animated.timing(tapRotate, { toValue: -0.8, duration: 65, useNativeDriver: true }),
            ]),
            Animated.parallel([
              Animated.timing(tapTranslateX, { toValue: 6, duration: 100, useNativeDriver: true }),
              Animated.timing(tapRotate, { toValue: 0.8, duration: 100, useNativeDriver: true }),
            ]),
            Animated.parallel([
              Animated.timing(tapTranslateX, { toValue: -3, duration: 80, useNativeDriver: true }),
              Animated.timing(tapRotate, { toValue: -0.4, duration: 80, useNativeDriver: true }),
            ]),
            Animated.parallel([
              Animated.spring(tapTranslateX, { toValue: 0, friction: 4, tension: 45, useNativeDriver: true }),
              Animated.spring(tapRotate, { toValue: 0, friction: 4, tension: 45, useNativeDriver: true }),
            ]),
          ]);
          break;
      }
    }

    activeTapAnimRef.current = tapAnim;
    tapAnim.start(() => {
      activeTapAnimRef.current = null;
    });
  };

  // Interpolated rotation strings
  const combinedRotate = Animated.add(
    Animated.add(loopRotate, microHeadTilt),
    tapRotate
  ).interpolate({
    inputRange: [-3, 3],
    outputRange: ['-12deg', '12deg'],
  });

  const combinedTranslateY = Animated.add(
    Animated.add(loopTranslateY, idleTranslateY),
    tapTranslateY
  );
  const combinedTranslateX = Animated.add(loopTranslateX, tapTranslateX);
  const combinedScale = Animated.multiply(
    Animated.multiply(loopScale, tapScale),
    stateScaleAnim
  );
  const combinedScaleX = loopScaleX;
  const combinedScaleY = loopScaleY;

  const currentAsset = getMascotAsset(displayedState || currentState);

  return (
    <View
      testID={testID}
      accessibilityRole={interactive ? 'button' : 'image'}
      accessibilityLabel={`Кот DOME: ${meta.titleRu}`}
      style={[
        styles.container,
        {
          width: containerWidth,
          height: containerHeight,
        },
        style,
      ]}
    >
      <Pressable
        disabled={!interactive}
        onPress={handlePress}
        hitSlop={6}
        style={styles.pressable}
      >
        <Animated.View
          style={[
            styles.animatedWrapper,
            {
              transform: [
                { translateX: combinedTranslateX },
                { translateY: combinedTranslateY },
                { scale: combinedScale },
                { scaleX: combinedScaleX },
                { scaleY: combinedScaleY },
                { rotate: combinedRotate },
              ],
            },
          ]}
        >
          {/* Current active asset — always guaranteed visible */}
          <Image
            source={currentAsset}
            resizeMode="contain"
            style={[
              styles.image,
              {
                width: containerWidth,
                height: containerHeight,
              },
            ]}
          />
        </Animated.View>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    position: 'relative',
    alignItems: 'center',
    justifyContent: 'center',
    overflow: 'visible',
  },
  pressable: {
    width: '100%',
    height: '100%',
    alignItems: 'center',
    justifyContent: 'center',
  },
  animatedWrapper: {
    width: '100%',
    height: '100%',
    alignItems: 'center',
    justifyContent: 'center',
  },
  image: {
    position: 'absolute',
  },
});
