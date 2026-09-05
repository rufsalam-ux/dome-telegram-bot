import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  BackHandler,
  Linking,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from 'react-native';
import {
  cancelSubscriptionPlanChange,
  confirmSubscriptionPlanChange,
  getSubscription,
  getSubscriptionPlanChangePreview,
  subscriptionCheckout,
  validatePromoCode,
  verifySubscription,
} from '../api/mobile';
import { useAppStore } from '../store/AppStore';

/* ─── types ─────────────────────────────────────────────────────────────── */
type BillingPeriod = 'MONTH' | 'YEAR';

type PromoResult = {
  valid: boolean;
  code: string;
  error?: string;
  benefit_type?: string;
  description?: string;
  original_price?: number;
  discount_amount?: number;
  final_price?: number;
  currency?: string;
  trial_days?: number;
  extra_lessons?: number;
};

type SubscriptionState =
  | 'selecting'
  | 'processing'
  | 'success'
  | 'failed'
  | 'cancelled'
  | 'already_subscribed'
  | 'owner';

/* ─── static plan data ────────────────────────────────────────────────────
   lesson_allowance is always per-month regardless of billing period.
   annual_price is charged once/year by PayPal (YEAR billing cycle).
   yearly_savings = monthly_price * 12 − annual_price
──────────────────────────────────────────────────────────────────────────── */
const PLANS = [
  {
    plan_id: 'weekly1',
    name: 'DOME Start',
    lessons_per_week: 1,
    lessons_per_month: 4,
    monthly_price: 39,
    annual_price: 399,
    annual_as_monthly: 33.25,
    yearly_savings: 69,
  },
  {
    plan_id: 'weekly2',
    name: 'DOME Smart',
    lessons_per_week: 2,
    lessons_per_month: 8,
    monthly_price: 69,
    annual_price: 699,
    annual_as_monthly: 58.25,
    yearly_savings: 129,
  },
  {
    plan_id: 'weekly3',
    name: 'DOME Plus',
    lessons_per_week: 3,
    lessons_per_month: 12,
    monthly_price: 99,
    annual_price: 999,
    annual_as_monthly: 83.25,
    yearly_savings: 189,
  },
  {
    plan_id: 'weekly4',
    name: 'DOME Max',
    lessons_per_week: 4,
    lessons_per_month: 16,
    monthly_price: 129,
    annual_price: 1299,
    annual_as_monthly: 108.25,
    yearly_savings: 249,
  },
] as const;

const fmt = (v: number) => `€${v % 1 === 0 ? v.toFixed(0) : v.toFixed(2)}`;

/* ─── component ───────────────────────────────────────────────────────────── */
export function PurchaseScreen({ onBack }: { onBack?: () => void } = {}) {
  const store = useAppStore();
  const child = store.selectedChild;

  const handleBackToMenu = useCallback(() => {
    if (onBack) {
      onBack();
    } else {
      store.setScreen('home');
    }
  }, [onBack, store]);

  useEffect(() => {
    const sub = BackHandler.addEventListener('hardwareBackPress', () => {
      handleBackToMenu();
      return true;
    });
    return () => sub.remove();
  }, [handleBackToMenu]);

  const [flowState, setFlowState] = useState<SubscriptionState>('selecting');
  const [selectedPlanId, setSelectedPlanId] = useState<string>('weekly2');
  const [billingPeriod, setBillingPeriod] = useState<BillingPeriod>('YEAR'); // annual by default
  const [promoCode, setPromoCode] = useState('');
  const [promoResult, setPromoResult] = useState<PromoResult | null>(null);
  const [promoLoading, setPromoLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [errorMsg, setErrorMsg] = useState('');
  const [providerNotConfigured, setProviderNotConfigured] = useState(false);
  const [existingSub, setExistingSub] = useState<any>(null);
  const [checkoutUrl, setCheckoutUrl] = useState('');
  const [pendingSubId, setPendingSubId] = useState('');
  const promoDebounce = useRef<ReturnType<typeof setTimeout> | null>(null);

  const selectedPlan = (PLANS.find(p => p.plan_id === selectedPlanId) ?? PLANS[1])!;

  // Base price for selected plan & period (what PayPal charges)
  const basePrice =
    billingPeriod === 'YEAR' ? selectedPlan.annual_price : selectedPlan.monthly_price;

  const effectivePrice =
    promoResult?.valid ? (promoResult.final_price ?? basePrice) : basePrice;

  const discount = promoResult?.valid ? (promoResult.discount_amount ?? 0) : 0;

  // Load existing subscription on mount
  const loadSub = useCallback(async () => {
    if (!child) return;
    try {
      const data = await getSubscription(child.id, child.courseId || 'conversation');
      const sub = data?.subscription;
      if (sub && sub.status === 'ACTIVE') {
        setExistingSub(sub);
        setFlowState('already_subscribed');
      }
    } catch { /* no sub yet */ }
  }, [child?.id, child?.courseId]);

  useEffect(() => { void loadSub(); }, [loadSub]);

  // Re-validate promo when plan or billing period changes
  useEffect(() => {
    if (promoDebounce.current) clearTimeout(promoDebounce.current);
    if (!promoCode.trim() || !child) {
      setPromoResult(null);
      return;
    }
    promoDebounce.current = setTimeout(async () => {
      setPromoLoading(true);
      try {
        const res = await validatePromoCode(
          child.id,
          promoCode.trim(),
          selectedPlanId,
          basePrice,
          billingPeriod,
        );
        setPromoResult(res);
      } catch {
        setPromoResult({ valid: false, code: promoCode, error: 'Ошибка проверки промокода' });
      } finally {
        setPromoLoading(false);
      }
    }, 600);
  }, [promoCode, selectedPlanId, basePrice, billingPeriod, child?.id]);

  if (!child) return null;

  /* ── Handlers ─────────────────────────────────────────────────────────── */
  const handleCheckout = async () => {
    if (flowState === 'already_subscribed') {
      Alert.alert('Уже есть подписка', 'Для смены тарифа используйте управление подпиской.');
      return;
    }
    setBusy(true);
    setErrorMsg('');
    try {
      setFlowState('processing');
      const res = await subscriptionCheckout(
        child.id,
        selectedPlanId,
        billingPeriod,
        child.courseId || 'conversation',
        promoResult?.valid ? promoCode.trim() : '',
      );

      if (res?.is_owner) { setFlowState('owner'); return; }

      if (!res?.configured || res?.error === 'PROVIDER_NOT_CONFIGURED') {
        setProviderNotConfigured(true);
        setFlowState('failed');
        setErrorMsg(res?.message || 'Платёжный провайдер в процессе настройки.');
        return;
      }

      if (!res?.ok) {
        setFlowState('failed');
        setErrorMsg(res?.message || 'Ошибка создания оплаты.');
        return;
      }

      const url = res.checkout_url;
      const subId = res.subscription_id || '';
      setCheckoutUrl(url);
      setPendingSubId(subId);

      if (url) {
        const supported = await Linking.canOpenURL(url);
        if (supported) {
          await Linking.openURL(url);
          await pollVerify(child.id, subId, child.courseId || 'conversation', promoResult?.valid ? promoCode.trim() : '');
        } else {
          setFlowState('failed');
          setErrorMsg('Не удалось открыть страницу PayPal.');
        }
      } else {
        setFlowState('failed');
        setErrorMsg('Нет ссылки на оплату.');
      }
    } catch (err: any) {
      setFlowState('failed');
      setErrorMsg(err?.message || 'Ошибка оформления подписки.');
    } finally {
      setBusy(false);
    }
  };

  const pollVerify = async (childId: any, subId: string, courseId: string, code: string) => {
    setBusy(true);
    try {
      const v = await verifySubscription(childId, subId, courseId, code);
      if (v?.active) {
        setFlowState('success');
      } else {
        setFlowState('cancelled');
        setErrorMsg('Оплата не была завершена в PayPal.');
      }
    } catch {
      setFlowState('cancelled');
    } finally {
      setBusy(false);
    }
  };

  const handleRetry = () => {
    setFlowState('selecting');
    setErrorMsg('');
    setProviderNotConfigured(false);
    setCheckoutUrl('');
    setPendingSubId('');
  };

  /* ── Terminal states ─────────────────────────────────────────────────── */
  if (flowState === 'owner') {
    return (
      <ScrollView contentContainerStyle={styles.container}>
        <TouchableOpacity style={styles.topBackBtn} onPress={handleBackToMenu} activeOpacity={0.7}>
          <Text style={styles.topBackBtnText}>← Назад в меню</Text>
        </TouchableOpacity>
        <Text style={styles.successIcon}>⭐</Text>
        <Text style={styles.h1}>Демо-доступ активен</Text>
        <Text style={styles.body}>Аккаунт Owner имеет неограниченный доступ ко всем занятиям без оплаты.</Text>
        <TouchableOpacity style={styles.primaryBtn} onPress={handleBackToMenu}>
          <Text style={styles.primaryBtnText}>← Назад в меню</Text>
        </TouchableOpacity>
      </ScrollView>
    );
  }

  if (flowState === 'success') {
    return (
      <ScrollView contentContainerStyle={styles.container}>
        <Text style={styles.successIcon}>🎉</Text>
        <Text style={styles.h1}>Подписка активирована!</Text>
        <Text style={styles.body}>Занятия уже доступны. Приятного обучения!</Text>
        <TouchableOpacity style={styles.primaryBtn} onPress={handleBackToMenu}>
          <Text style={styles.primaryBtnText}>← К занятиям в меню</Text>
        </TouchableOpacity>
      </ScrollView>
    );
  }

  if (flowState === 'already_subscribed' && existingSub) {
    const planName = PLANS.find(p => p.plan_id === (existingSub.current_plan_id || existingSub.plan_id))?.name
      ?? (existingSub.current_plan_id || existingSub.plan_id);
    const bp = (existingSub.billing_period || 'MONTH') as BillingPeriod;
    const bpLabel = bp === 'YEAR' ? 'Годовая' : 'Ежемесячная';
    return (
      <ScrollView contentContainerStyle={styles.container}>
        <TouchableOpacity style={styles.topBackBtn} onPress={handleBackToMenu} activeOpacity={0.7}>
          <Text style={styles.topBackBtnText}>← Назад в меню</Text>
        </TouchableOpacity>
        <Text style={styles.h1}>Ваша подписка</Text>
        <View style={styles.subCard}>
          <Text style={styles.subCardLabel}>Тариф</Text>
          <Text style={styles.subCardValue}>{planName}</Text>
          <Text style={styles.subCardLabel}>Уроков / месяц</Text>
          <Text style={styles.subCardValue}>{(existingSub.lessons_per_week ?? 0) * 4}</Text>
          <Text style={styles.subCardLabel}>Оплата</Text>
          <Text style={styles.subCardValue}>{bpLabel}</Text>
          <Text style={styles.subCardLabel}>Статус</Text>
          <Text style={[styles.subCardValue, { color: '#22c55e' }]}>Активна</Text>
        </View>
        <Text style={[styles.body, { marginTop: 16 }]}>
          Для смены тарифа обратитесь в поддержку или перейдите в управление подпиской.
        </Text>
        <TouchableOpacity style={styles.secondaryBtn} onPress={handleBackToMenu}>
          <Text style={styles.secondaryBtnText}>← Назад в меню</Text>
        </TouchableOpacity>
      </ScrollView>
    );
  }

  if (flowState === 'processing') {
    return (
      <ScrollView contentContainerStyle={styles.container}>
        <ActivityIndicator size="large" color="#7df3e1" style={{ marginBottom: 20 }} />
        <Text style={styles.h1}>Переход к PayPal...</Text>
        <Text style={styles.body}>
          После завершения оплаты вернитесь в приложение. Подписка активируется автоматически.
        </Text>
        {checkoutUrl ? (
          <TouchableOpacity style={styles.secondaryBtn} onPress={() => Linking.openURL(checkoutUrl)}>
            <Text style={styles.secondaryBtnText}>Открыть PayPal снова</Text>
          </TouchableOpacity>
        ) : null}
        <TouchableOpacity style={[styles.secondaryBtn, { marginTop: 16 }]} onPress={handleBackToMenu}>
          <Text style={styles.secondaryBtnText}>← Отмена / Назад в меню</Text>
        </TouchableOpacity>
      </ScrollView>
    );
  }

  if (flowState === 'failed' || flowState === 'cancelled') {
    return (
      <ScrollView contentContainerStyle={styles.container}>
        <Text style={styles.failIcon}>{flowState === 'cancelled' ? 'ℹ️' : '❌'}</Text>
        <Text style={styles.h1}>{flowState === 'cancelled' ? 'Оплата отменена' : 'Ошибка оплаты'}</Text>
        {providerNotConfigured ? (
          <View style={styles.warningBox}>
            <Text style={styles.warningTitle}>Платёжный провайдер в режиме настройки</Text>
            <Text style={styles.warningBody}>
              Укажите PAYPAL_CLIENT_ID и PAYPAL_CLIENT_SECRET в переменных окружения backend.
            </Text>
          </View>
        ) : null}
        {errorMsg ? <Text style={styles.errorText}>{errorMsg}</Text> : null}
        <TouchableOpacity style={styles.primaryBtn} onPress={handleRetry}>
          <Text style={styles.primaryBtnText}>Попробовать снова</Text>
        </TouchableOpacity>
        <TouchableOpacity style={styles.secondaryBtn} onPress={handleBackToMenu}>
          <Text style={styles.secondaryBtnText}>← Назад в главное меню</Text>
        </TouchableOpacity>
      </ScrollView>
    );
  }

  /* ── Main plan selection ─────────────────────────────────────────────── */
  const isAnnual = billingPeriod === 'YEAR';
  const nextRenewal = isAnnual ? 'Следующее продление: через 12 месяцев' : 'Следующее продление: через 1 месяц';

  return (
    <ScrollView contentContainerStyle={styles.container} keyboardShouldPersistTaps="handled">
      {/* ── Top Back button ───────────────────────────────────────────── */}
      <TouchableOpacity
        style={styles.topBackBtn}
        onPress={handleBackToMenu}
        activeOpacity={0.7}
        accessibilityRole="button"
        accessibilityLabel="Назад в меню"
      >
        <Text style={styles.topBackBtnText}>← Назад в меню</Text>
      </TouchableOpacity>

      <Text style={styles.h1}>Выберите тариф</Text>
      <Text style={styles.subtitle}>Персональный AI-репетитор для вашего ребёнка. Отмена в любое время.</Text>

      {/* ── Billing Period Toggle ─────────────────────────────────────── */}
      <View style={styles.toggleRow}>
        <TouchableOpacity
          style={[styles.toggleBtn, !isAnnual && styles.toggleBtnActive]}
          onPress={() => { setBillingPeriod('MONTH'); setPromoResult(null); }}
          activeOpacity={0.8}
        >
          <Text style={[styles.toggleBtnText, !isAnnual && styles.toggleBtnTextActive]}>
            Ежемесячно
          </Text>
        </TouchableOpacity>
        <TouchableOpacity
          style={[styles.toggleBtn, isAnnual && styles.toggleBtnActiveGreen]}
          onPress={() => { setBillingPeriod('YEAR'); setPromoResult(null); }}
          activeOpacity={0.8}
        >
          <Text style={[styles.toggleBtnText, isAnnual && styles.toggleBtnTextActive]}>
            {'⭐ Годовая — Выгоднее'}
          </Text>
        </TouchableOpacity>
      </View>

      {/* ── Plan cards ───────────────────────────────────────────────── */}
      {PLANS.map(plan => {
        const isSelected = plan.plan_id === selectedPlanId;
        const promoApplied = isSelected && promoResult?.valid;
        const displayPrice = promoApplied
          ? (promoResult!.final_price ?? basePrice)
          : (isAnnual ? plan.annual_price : plan.monthly_price);
        const discountAmt = isSelected && promoResult?.valid ? (promoResult.discount_amount ?? 0) : 0;

        return (
          <TouchableOpacity
            key={plan.plan_id}
            style={[styles.planCard, isSelected && styles.planCardSelected]}
            onPress={() => setSelectedPlanId(plan.plan_id)}
            activeOpacity={0.8}
          >
            {/* Annual recommended badge */}
            {isAnnual && (
              <View style={styles.savingsBadge}>
                <Text style={styles.savingsBadgeText}>ЭКОНОМИЯ {fmt(plan.yearly_savings)}/год</Text>
              </View>
            )}

            {/* Plan name & lessons */}
            <Text style={[styles.planName, isSelected && styles.planNameSelected]}>
              {plan.name}
            </Text>
            <Text style={styles.planLessons}>{plan.lessons_per_month} урока / месяц</Text>

            <View style={styles.priceBlock}>
              {isAnnual ? (
                /* Annual pricing — prominent */
                <>
                  <View style={styles.priceMainRow}>
                    <Text style={[styles.priceMain, isSelected && styles.priceMainSelected]}>
                      {fmt(displayPrice)}
                    </Text>
                    <Text style={styles.pricePeriod}> / год</Text>
                  </View>
                  <Text style={styles.priceAsMonthly}>
                    ≈ {fmt(plan.annual_as_monthly)} / месяц
                  </Text>
                  {/* Strikethrough monthly comparison */}
                  <Text style={styles.priceMonthlyStrike}>
                    вместо {fmt(plan.monthly_price)} / мес
                  </Text>
                  {discountAmt > 0 && (
                    <Text style={styles.promoSavingsLine}>
                      🏷 Промокод −{fmt(discountAmt)} → итого {fmt(displayPrice)}/год
                    </Text>
                  )}
                </>
              ) : (
                /* Monthly pricing */
                <>
                  <View style={styles.priceMainRow}>
                    <Text style={[styles.priceMain, isSelected && styles.priceMainSelected]}>
                      {fmt(displayPrice)}
                    </Text>
                    <Text style={styles.pricePeriod}> / мес</Text>
                  </View>
                  {/* Show annual comparison as upsell */}
                  <Text style={styles.priceAnnualHint}>
                    {fmt(plan.annual_price)}/год → экономия {fmt(plan.yearly_savings)}
                  </Text>
                  {discountAmt > 0 && (
                    <Text style={styles.promoSavingsLine}>
                      🏷 Промокод −{fmt(discountAmt)} → итого {fmt(displayPrice)}/мес
                    </Text>
                  )}
                </>
              )}
            </View>

            {/* Selection dot */}
            {isSelected ? <View style={styles.planSelectedDot} /> : null}
          </TouchableOpacity>
        );
      })}

      {/* ── Promo code ───────────────────────────────────────────────── */}
      <View style={styles.promoRow}>
        <TextInput
          style={styles.promoInput}
          placeholder="Промокод (необязательно)"
          placeholderTextColor="#64748b"
          value={promoCode}
          onChangeText={setPromoCode}
          autoCapitalize="characters"
          autoCorrect={false}
        />
        {promoLoading ? (
          <ActivityIndicator size="small" color="#7df3e1" style={{ marginLeft: 10 }} />
        ) : null}
      </View>

      {promoResult ? (
        <View style={[styles.promoResult, promoResult.valid ? styles.promoResultOk : styles.promoResultErr]}>
          {promoResult.valid ? (
            <>
              <Text style={styles.promoResultIcon}>✅</Text>
              <View style={{ flex: 1 }}>
                <Text style={styles.promoResultText}>{promoResult.description}</Text>
                {(promoResult.discount_amount ?? 0) > 0 ? (
                  <Text style={styles.promoResultSub}>
                    Обычная цена {fmt(promoResult.original_price ?? 0)} → промокод −{fmt(promoResult.discount_amount ?? 0)} → итого {fmt(promoResult.final_price ?? 0)}
                  </Text>
                ) : null}
              </View>
            </>
          ) : (
            <>
              <Text style={styles.promoResultIcon}>❌</Text>
              <Text style={[styles.promoResultText, { color: '#ef4444' }]}>{promoResult.error}</Text>
            </>
          )}
        </View>
      ) : null}

      {/* ── Checkout summary ─────────────────────────────────────────── */}
      <View style={styles.summaryBox}>
        <Text style={styles.summaryTitle}>Итог оформления</Text>

        <View style={styles.summaryRow}>
          <Text style={styles.summaryLabel}>Тариф</Text>
          <Text style={styles.summaryValue}>{selectedPlan.name}</Text>
        </View>

        <View style={styles.summaryRow}>
          <Text style={styles.summaryLabel}>Уроков / месяц</Text>
          <Text style={styles.summaryValue}>{selectedPlan.lessons_per_month}</Text>
        </View>

        <View style={styles.summaryRow}>
          <Text style={styles.summaryLabel}>Оплата</Text>
          <Text style={styles.summaryValue}>{isAnnual ? 'Годовая подписка' : 'Ежемесячная подписка'}</Text>
        </View>

        {discount > 0 ? (
          <View style={styles.summaryRow}>
            <Text style={styles.summaryLabel}>Промокод</Text>
            <Text style={[styles.summaryValue, { color: '#22c55e' }]}>−{fmt(discount)}</Text>
          </View>
        ) : null}

        <View style={[styles.summaryRow, styles.summaryTotalRow]}>
          <Text style={styles.summaryTotalLabel}>Сегодня</Text>
          <Text style={styles.summaryTotalValue}>
            {fmt(effectivePrice)}{isAnnual ? '/год' : '/мес'}
          </Text>
        </View>

        <Text style={styles.summaryRenewal}>{nextRenewal}</Text>
      </View>

      {/* ── PayPal checkout button ────────────────────────────────────── */}
      <TouchableOpacity
        style={[styles.paypalBtn, busy && { opacity: 0.6 }]}
        onPress={handleCheckout}
        disabled={busy}
        activeOpacity={0.85}
      >
        {busy ? (
          <ActivityIndicator size="small" color="#fff" />
        ) : (
          <>
            <Text style={styles.paypalBtnText}>
              Оплатить {fmt(effectivePrice)}{isAnnual ? '/год' : '/мес'} через PayPal
            </Text>
            <Text style={styles.paypalBtnSub}>
              {isAnnual ? '🔒 Безопасный платёж · Годовая подписка' : '🔒 Безопасный платёж'}
            </Text>
          </>
        )}
      </TouchableOpacity>

      {/* ── Disclaimer ───────────────────────────────────────────────── */}
      <Text style={styles.disclaimer}>
        {isAnnual
          ? `Годовая подписка: списание ${fmt(effectivePrice)} один раз в год. Уроки начисляются ежемесячно (${selectedPlan.lessons_per_month}/мес). Отмена в любое время через PayPal.`
          : `Ежемесячная подписка: ${fmt(effectivePrice)}/мес, автопродление каждый месяц. Отмена в любое время.`}
        {' '}Цены в EUR.
      </Text>

      {/* ── Bottom Back button ────────────────────────────────────────── */}
      <TouchableOpacity
        style={styles.bottomBackBtn}
        onPress={handleBackToMenu}
        activeOpacity={0.7}
      >
        <Text style={styles.bottomBackBtnText}>← Назад в главное меню</Text>
      </TouchableOpacity>
    </ScrollView>
  );
}

/* ─── styles ──────────────────────────────────────────────────────────────── */
const TEAL = '#7df3e1';
const TEAL_DARK = '#4fd1b5';
const BG = '#0f172a';
const CARD = '#1e293b';
const CARD_SELECTED = '#0f3b4f';
const TEXT = '#f1f5f9';
const MUTED = '#94a3b8';
const GREEN = '#22c55e';
const YELLOW = '#fbbf24';

const styles = StyleSheet.create({
  container: {
    flexGrow: 1,
    backgroundColor: BG,
    padding: 20,
    paddingBottom: 40,
  },
  topBackBtn: {
    alignSelf: 'flex-start',
    paddingVertical: 8,
    paddingHorizontal: 16,
    borderRadius: 12,
    backgroundColor: '#1e293b',
    borderWidth: 1,
    borderColor: '#334155',
    marginBottom: 16,
  },
  topBackBtnText: {
    color: TEAL,
    fontSize: 14,
    fontWeight: '700',
  },
  bottomBackBtn: {
    alignSelf: 'center',
    paddingVertical: 12,
    paddingHorizontal: 24,
    borderRadius: 12,
    backgroundColor: '#1e293b',
    borderWidth: 1,
    borderColor: '#334155',
    marginTop: 18,
    marginBottom: 8,
  },
  bottomBackBtnText: {
    color: TEAL,
    fontSize: 15,
    fontWeight: '700',
  },
  h1: {
    fontSize: 26,
    fontWeight: '800',
    color: TEXT,
    textAlign: 'center',
    marginBottom: 8,
  },
  subtitle: {
    fontSize: 14,
    color: MUTED,
    textAlign: 'center',
    marginBottom: 24,
    lineHeight: 20,
  },

  /* ── Billing toggle */
  toggleRow: {
    flexDirection: 'row',
    backgroundColor: '#1e293b',
    borderRadius: 12,
    padding: 4,
    marginBottom: 20,
    gap: 4,
  },
  toggleBtn: {
    flex: 1,
    paddingVertical: 10,
    paddingHorizontal: 8,
    borderRadius: 10,
    alignItems: 'center',
    justifyContent: 'center',
  },
  toggleBtnActive: {
    backgroundColor: '#334155',
  },
  toggleBtnActiveGreen: {
    backgroundColor: TEAL_DARK,
  },
  toggleBtnText: {
    color: MUTED,
    fontWeight: '600',
    fontSize: 13,
    textAlign: 'center',
  },
  toggleBtnTextActive: {
    color: '#0f172a',
    fontWeight: '800',
  },

  /* ── Plan card */
  planCard: {
    backgroundColor: CARD,
    borderRadius: 16,
    padding: 16,
    marginBottom: 12,
    borderWidth: 2,
    borderColor: 'transparent',
    position: 'relative',
    overflow: 'hidden',
  },
  planCardSelected: {
    borderColor: TEAL,
    backgroundColor: CARD_SELECTED,
  },
  savingsBadge: {
    position: 'absolute',
    top: 0,
    right: 0,
    backgroundColor: GREEN,
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderBottomLeftRadius: 10,
  },
  savingsBadgeText: {
    color: '#fff',
    fontWeight: '800',
    fontSize: 11,
  },
  planName: {
    fontSize: 18,
    fontWeight: '700',
    color: TEXT,
    marginBottom: 2,
    marginTop: 2,
  },
  planNameSelected: {
    color: TEAL,
  },
  planLessons: {
    fontSize: 13,
    color: MUTED,
    marginBottom: 12,
  },
  priceBlock: {
    marginTop: 4,
  },
  priceMainRow: {
    flexDirection: 'row',
    alignItems: 'baseline',
  },
  priceMain: {
    fontSize: 32,
    fontWeight: '800',
    color: TEXT,
  },
  priceMainSelected: {
    color: TEAL,
  },
  pricePeriod: {
    fontSize: 16,
    color: MUTED,
    fontWeight: '600',
  },
  priceAsMonthly: {
    fontSize: 13,
    color: TEAL,
    fontWeight: '600',
    marginTop: 2,
  },
  priceMonthlyStrike: {
    fontSize: 12,
    color: MUTED,
    textDecorationLine: 'line-through',
    marginTop: 2,
  },
  priceAnnualHint: {
    fontSize: 12,
    color: YELLOW,
    marginTop: 4,
  },
  promoSavingsLine: {
    fontSize: 12,
    color: GREEN,
    marginTop: 4,
    fontWeight: '600',
  },
  planSelectedDot: {
    width: 10,
    height: 10,
    borderRadius: 5,
    backgroundColor: TEAL,
    position: 'absolute',
    top: 16,
    left: 16,
  },

  /* ── Promo */
  promoRow: {
    flexDirection: 'row',
    alignItems: 'center',
    marginTop: 8,
    marginBottom: 4,
  },
  promoInput: {
    flex: 1,
    backgroundColor: CARD,
    borderRadius: 12,
    paddingHorizontal: 14,
    paddingVertical: 12,
    color: TEXT,
    fontSize: 14,
    borderWidth: 1,
    borderColor: '#334155',
    letterSpacing: 2,
  },
  promoResult: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    borderRadius: 10,
    padding: 12,
    marginBottom: 8,
    gap: 10,
  },
  promoResultOk: { backgroundColor: '#14532d' },
  promoResultErr: { backgroundColor: '#450a0a' },
  promoResultIcon: { fontSize: 18 },
  promoResultText: { color: TEXT, fontSize: 13, fontWeight: '600' },
  promoResultSub: { color: TEAL, fontSize: 12, marginTop: 2 },

  /* ── Summary box */
  summaryBox: {
    backgroundColor: CARD,
    borderRadius: 14,
    padding: 16,
    marginTop: 8,
    marginBottom: 16,
    borderWidth: 1,
    borderColor: '#334155',
  },
  summaryTitle: {
    fontSize: 14,
    fontWeight: '700',
    color: MUTED,
    marginBottom: 12,
    textTransform: 'uppercase',
    letterSpacing: 1,
  },
  summaryRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 8,
  },
  summaryLabel: {
    fontSize: 14,
    color: MUTED,
    flex: 1,
  },
  summaryValue: {
    fontSize: 14,
    color: TEXT,
    fontWeight: '600',
    textAlign: 'right',
  },
  summaryTotalRow: {
    borderTopWidth: 1,
    borderTopColor: '#334155',
    paddingTop: 10,
    marginTop: 4,
    marginBottom: 4,
  },
  summaryTotalLabel: {
    fontSize: 16,
    color: TEXT,
    fontWeight: '700',
  },
  summaryTotalValue: {
    fontSize: 22,
    color: TEAL,
    fontWeight: '800',
  },
  summaryRenewal: {
    fontSize: 12,
    color: MUTED,
    marginTop: 6,
    textAlign: 'right',
  },

  /* ── PayPal button */
  paypalBtn: {
    backgroundColor: '#003087',
    borderRadius: 14,
    paddingVertical: 16,
    paddingHorizontal: 20,
    alignItems: 'center',
    marginBottom: 12,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.3,
    shadowRadius: 8,
    elevation: 6,
  },
  paypalBtnText: {
    color: '#fff',
    fontSize: 17,
    fontWeight: '800',
    letterSpacing: 0.3,
  },
  paypalBtnSub: {
    color: '#7ec8e3',
    fontSize: 12,
    marginTop: 4,
    fontWeight: '500',
  },

  /* ── Other */
  disclaimer: {
    fontSize: 11,
    color: MUTED,
    textAlign: 'center',
    lineHeight: 16,
    marginTop: 4,
  },
  primaryBtn: {
    backgroundColor: TEAL,
    borderRadius: 12,
    paddingVertical: 14,
    paddingHorizontal: 24,
    alignItems: 'center',
    marginTop: 16,
  },
  primaryBtnText: {
    color: '#0f172a',
    fontWeight: '800',
    fontSize: 16,
  },
  secondaryBtn: {
    borderWidth: 1.5,
    borderColor: TEAL,
    borderRadius: 12,
    paddingVertical: 12,
    paddingHorizontal: 24,
    alignItems: 'center',
    marginTop: 12,
  },
  secondaryBtnText: {
    color: TEAL,
    fontWeight: '700',
    fontSize: 15,
  },
  successIcon: { fontSize: 56, textAlign: 'center', marginBottom: 16 },
  failIcon: { fontSize: 56, textAlign: 'center', marginBottom: 16 },
  body: {
    color: MUTED,
    fontSize: 15,
    textAlign: 'center',
    lineHeight: 22,
  },
  errorText: {
    color: '#ef4444',
    fontSize: 14,
    textAlign: 'center',
    marginVertical: 12,
  },
  warningBox: {
    backgroundColor: '#422006',
    borderRadius: 10,
    padding: 14,
    marginBottom: 12,
  },
  warningTitle: {
    color: YELLOW,
    fontWeight: '700',
    fontSize: 14,
    marginBottom: 4,
  },
  warningBody: {
    color: '#fde68a',
    fontSize: 13,
    lineHeight: 18,
  },
  subCard: {
    backgroundColor: CARD,
    borderRadius: 14,
    padding: 16,
    width: '100%',
    borderWidth: 1,
    borderColor: '#334155',
  },
  subCardLabel: {
    color: MUTED,
    fontSize: 12,
    textTransform: 'uppercase',
    letterSpacing: 1,
    marginTop: 10,
    marginBottom: 2,
  },
  subCardValue: {
    color: TEXT,
    fontSize: 16,
    fontWeight: '700',
  },
});
