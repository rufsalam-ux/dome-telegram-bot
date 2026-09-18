import React, { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  Linking,
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { Body, Button, Card, H1, H2 } from '../components/Ui';
import {
  cancelSubscriptionPlanChange,
  confirmSubscriptionPlanChange,
  getSubscription,
  getSubscriptionPlanChangePreview,
  subscriptionCheckout,
} from '../api/mobile';
import { useAppStore } from '../store/AppStore';
import { theme } from '../theme/theme';

export type Plan = {
  plan_id: string;
  version_id: string;
  title: string;
  lessons_per_week: number;
  price: number;
  currency: string;
  billing_period: string;
  // Special annual pricing fields (server-provided, YEAR plans only)
  special_first_year?: boolean;
  first_year_price?: number;
  standard_annual_price?: number;
  standard_renewal_price?: number;
  annual_savings?: number;
  intro_week_price?: number;
  renewal_disclosure?: string;
  title_badge?: string;
};

type Preview = {
  current_plan: Plan;
  new_plan: Plan;
  effective_at: string;
  notice: string;
};

// Canonical DOME Pricing (server is source of truth for prices)
// Monthly: weekly1=39, weekly2=69, weekly3=99, weekly4=139 EUR/month
// Standard Annual: weekly1=439, weekly2=759, weekly3=1089, weekly4=1535 EUR/year
// Special First Year (new customers): weekly1=349, weekly2=599, weekly3=849, weekly4=1199 EUR
// Intro week: weekly1=3, weekly2=6, weekly3=9, weekly4=12 EUR
const PLAN_DETAILS: Record<
  number,
  { name: string; introPrice: number; subtitle: string }
> = {
  1: {
    name: 'DOME Start',
    introPrice: 3,
    subtitle: '1 занятие в неделю · плавный старт и регулярная практика',
  },
  2: {
    name: 'DOME Smart',
    introPrice: 6,
    subtitle: '2 занятия в неделю · самый популярный и эффективный темп',
  },
  3: {
    name: 'DOME Plus',
    introPrice: 9,
    subtitle: '3 занятия в неделю · ускоренный прогресс и уверенность',
  },
  4: {
    name: 'DOME Max',
    introPrice: 12,
    subtitle: '4 занятия в неделю · полное погружение для билингвов',
  },
};

const date = (value?: string | null) =>
  value ? new Date(value).toLocaleDateString('ru-RU') : '—';
const money = (value: number | undefined, currency: string | undefined) =>
  `${Number(value || 0).toFixed(2)} ${currency || 'EUR'}`;
const periodLabel = (value?: string) => (value === 'YEAR' ? 'год' : 'месяц');

export function PurchaseScreen({ onBack }: { onBack?: () => void }) {
  const store = useAppStore();
  const child = store.selectedChild;
  const isOwner =
    Boolean(store.parent?.isOwner) ||
    String(store.parent?.email || '').trim().toLowerCase().includes('krisriskrisris');

  const [data, setData] = useState<any>(null);
  const [selectedPeriod, setSelectedPeriod] = useState<'MONTH' | 'YEAR'>('MONTH');
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);
  const [legalModalDoc, setLegalModalDoc] = useState<{ title: string; body: string } | null>(null);

  const load = useCallback(async () => {
    if (!child) return;
    try {
      setBusy(true);
      const res = await getSubscription(child.id, child.courseId || 'conversation');
      setData(res);
      if (res?.subscription?.current_plan?.billing_period === 'YEAR') {
        setSelectedPeriod('YEAR');
      }
    } catch (error: any) {
      Alert.alert('Не удалось загрузить тариф', error.message || 'Ошибка сети');
    } finally {
      setBusy(false);
    }
  }, [child?.id, child?.courseId]);

  useEffect(() => {
    void load();
  }, [load]);

  if (!child) return null;

  const subscription = data?.subscription;
  const current = subscription?.current_plan as Plan | undefined;
  const pending = subscription?.pending_plan;
  const plans = ((data?.plans || []) as Plan[]).filter(
    (p) => p.billing_period === selectedPeriod
  );

  const handleSelectPlan = async (plan: Plan) => {
    // If active subscription exists -> request change preview
    if (subscription && subscription.status === 'ACTIVE') {
      try {
        setBusy(true);
        const res = await getSubscriptionPlanChangePreview(
          child.id,
          plan.plan_id,
          plan.billing_period,
          plan.version_id,
          child.courseId || 'conversation'
        );
        setPreview(res);
      } catch (error: any) {
        Alert.alert('Не удалось выбрать тариф', error.message || 'Попробуйте позже');
      } finally {
        setBusy(false);
      }
      return;
    }

    // Otherwise -> new subscription checkout flow
    try {
      setBusy(true);
      const checkoutRes = await subscriptionCheckout(
        child.id,
        plan.plan_id,
        plan.billing_period,
        child.courseId || 'conversation'
      );
      if (checkoutRes.is_owner || isOwner) {
        Alert.alert('Доступ открыт', 'Для вашего аккаунта действует постоянный полный доступ ко всем урокам!');
        await load();
        return;
      }
      if (checkoutRes.checkout_url) {
        Alert.alert('Оплата подписки', 'Вы будете перенаправлены для подтверждения подписки.');
        await Linking.openURL(checkoutRes.checkout_url);
      } else if (checkoutRes.message) {
        Alert.alert('Подписка', checkoutRes.message);
        await load();
      }
    } catch (error: any) {
      Alert.alert('Не удалось оформить подписку', error.message || 'Попробуйте позже');
    } finally {
      setBusy(false);
    }
  };

  const confirmChange = async () => {
    if (!preview) return;
    try {
      setBusy(true);
      const result = await confirmSubscriptionPlanChange(
        child.id,
        preview.new_plan.plan_id,
        preview.new_plan.billing_period,
        preview.new_plan.version_id,
        child.courseId || 'conversation'
      );
      setPreview(null);
      setData((currentData: any) => ({
        ...currentData,
        subscription: result.subscription,
      }));
      if (result.approval_url) {
        Alert.alert(
          'Нужно подтверждение',
          'Подтвердите смену тарифа в платежном провайдере. До подтверждения текущий тариф остается без изменений.'
        );
        await Linking.openURL(result.approval_url);
      } else {
        Alert.alert('Готово', result.message || 'Тариф успешно обновлен');
      }
    } catch (error: any) {
      Alert.alert('Не удалось изменить тариф', error.message || 'Ошибка смены тарифа');
    } finally {
      setBusy(false);
    }
  };

  const cancelChange = async () => {
    try {
      setBusy(true);
      const result = await cancelSubscriptionPlanChange(
        child.id,
        child.courseId || 'conversation'
      );
      setPreview(null);
      setData((currentData: any) => ({
        ...currentData,
        subscription: result.subscription,
      }));
      if (result.approval_url) {
        Alert.alert('Нужно подтверждение', result.message);
        await Linking.openURL(result.approval_url);
      } else {
        Alert.alert('Готово', result.message || 'Изменение отменено');
      }
    } catch (error: any) {
      Alert.alert('Не удалось отменить изменение', error.message || 'Ошибка');
    } finally {
      setBusy(false);
    }
  };

  const handleBack = () => {
    if (onBack) {
      onBack();
    } else {
      store.setScreen('home');
    }
  };

  return (
    <ScrollView contentContainerStyle={{ padding: 20, paddingBottom: 40 }}>
      <H1>Тарифы DOME</H1>

      {isOwner ? (
        <Card>
          <View style={styles.ownerBadge}>
            <Text style={styles.ownerBadgeText}>★ РЕЖИМ ВЛАДЕЛЬЦА (OWNER)</Text>
          </View>
          <H2>Постоянный неограниченный доступ</H2>
          <Body>
            Для вашего аккаунта ({store.parent?.email}) открыты все уроки, функции, персонажные
            мультфильмы и генерации без ограничений и оплат.
          </Body>
        </Card>
      ) : null}

      {busy && !data ? (
        <Card>
          <Body>Загружаем тарифы и статус подписки…</Body>
        </Card>
      ) : null}

      {subscription && current ? (
        <Card>
          <H2>Текущий тариф: {current.title}</H2>
          <Body>Уроков в неделю: {current.lessons_per_week}</Body>
          <Body>
            Текущая цена: {money(current.price, current.currency)} за{' '}
            {periodLabel(current.billing_period)}
          </Body>
          <Body>Следующая дата списания: {date(subscription.next_charge_at)}</Body>
          <Body>Статус подписки: {subscription.status}</Body>
        </Card>
      ) : null}

      {pending ? (
        <Card>
          <H2>Запланировано изменение</H2>
          <Body>Новый тариф: {pending.lessons_per_week} урок(а) в неделю</Body>
          <Body>
            Стоимость следующего периода: {money(pending.price, pending.currency)} за{' '}
            {periodLabel(pending.billing_period)}
          </Body>
          <Body>Начнёт действовать: {date(pending.effective_at)}</Body>
          {pending.provider_status === 'PENDING_APPROVAL' ? (
            <Body>Ожидается подтверждение изменения в платежной системе.</Body>
          ) : null}
          {pending.provider_status === 'CANCEL_PENDING_APPROVAL' ? (
            <Body>Ожидается подтверждение отмены в платежной системе.</Body>
          ) : null}
          <Button disabled={busy} secondary title="Отменить изменение тарифа" onPress={cancelChange} />
        </Card>
      ) : null}

      {/* Period Toggle Switcher */}
      <View style={styles.toggleContainer}>
        <Pressable
          accessibilityRole="button"
          onPress={() => setSelectedPeriod('MONTH')}
          style={[
            styles.toggleButton,
            selectedPeriod === 'MONTH' ? styles.toggleButtonActive : null,
          ]}
        >
          <Text
            style={[
              styles.toggleButtonText,
              selectedPeriod === 'MONTH' ? styles.toggleButtonTextActive : null,
            ]}
          >
            Месячные
          </Text>
        </Pressable>

        <Pressable
          accessibilityRole="button"
          onPress={() => setSelectedPeriod('YEAR')}
          style={[
            styles.toggleButton,
            selectedPeriod === 'YEAR' ? styles.toggleButtonActive : null,
          ]}
        >
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
            <Text
              style={[
                styles.toggleButtonText,
                selectedPeriod === 'YEAR' ? styles.toggleButtonTextActive : null,
              ]}
            >
              Годовые
            </Text>
            <View style={styles.savingsTag}>
              <Text style={styles.savingsTagText}>-16%</Text>
            </View>
          </View>
        </Pressable>
      </View>

      {/* Intro Banner for Monthly */}
      {selectedPeriod === 'MONTH' ? (
        <View style={styles.introBanner}>
          <Text style={styles.introBannerTitle}>🔥 Пробная неделя: всего 3 € за занятие</Text>
          <Text style={styles.introBannerSub}>
            Оцените интерактивные уроки DOME с AI-ведущей и персональным мультфильмом. Далее —
            регулярная ежемесячная подписка.
          </Text>
        </View>
      ) : (
        <View style={data?.special_annual_eligible ? styles.annualBannerSpecial : styles.annualBanner}>
          <Text style={data?.special_annual_eligible ? styles.annualBannerTitleSpecial : styles.annualBannerTitle}>
            {data?.special_annual_eligible ? '🌟 Специальная цена первого года' : '💎 Выгода годового тарифа'}
          </Text>
          <Text style={data?.special_annual_eligible ? styles.annualBannerSubSpecial : styles.annualBannerSub}>
            {data?.special_annual_eligible
              ? 'Вы можете оформить первый год по специальной цене. Затем подписка автоматически продлевается по стандартной стоимости.'
              : '2+ месяца занятий бесплатно! Фиксированная годовая цена защищает от повышений на весь год.'}
          </Text>
        </View>
      )}

      {/* Plans List */}
      {!preview ? (
        <View style={{ marginTop: 12 }}>
          {plans.map((plan) => {
            const detail = PLAN_DETAILS[plan.lessons_per_week] || {
              name: plan.title,
              introPrice: plan.lessons_per_week * 3,
              subtitle: `${plan.lessons_per_week} урок(а) в неделю`,
            };

            const isCurrent =
              subscription &&
              current &&
              plan.plan_id === current.plan_id &&
              plan.billing_period === current.billing_period;
            const isPending = pending && plan.version_id === pending.version_id;

            const isSpecialAnnual = selectedPeriod === 'YEAR' && Boolean(plan.special_first_year);
            const effectivePrice = isSpecialAnnual && plan.first_year_price != null
              ? plan.first_year_price
              : plan.price;
            const displayCurrency = plan.currency || 'EUR';

            return (
              <Card key={plan.version_id}>
                {/* Title badge — "Специальная цена первого года" or "Годовой тариф" for YEAR plans */}
                {selectedPeriod === 'YEAR' && plan.title_badge ? (
                  <View style={isSpecialAnnual ? styles.specialBadge : styles.annualBadge}>
                    <Text style={isSpecialAnnual ? styles.specialBadgeText : styles.annualBadgeText}>
                      {plan.title_badge}
                    </Text>
                  </View>
                ) : null}

                <View style={styles.planCardHeader}>
                  <Text style={styles.planTitle}>{detail.name}</Text>
                  {selectedPeriod === 'YEAR' && isSpecialAnnual ? (
                    <View style={{ alignItems: 'flex-end' }}>
                      {/* Strikethrough standard price */}
                      <Text style={styles.planPriceStrikethrough}>
                        {money(plan.standard_annual_price ?? plan.standard_renewal_price, displayCurrency)} / год
                      </Text>
                      {/* Special first-year price */}
                      <Text style={styles.planPriceSpecial}>
                        {money(effectivePrice, displayCurrency)} / год
                      </Text>
                    </View>
                  ) : (
                    <Text style={styles.planPriceBadge}>
                      {money(effectivePrice, displayCurrency)} / {periodLabel(plan.billing_period)}
                    </Text>
                  )}
                </View>

                <Text style={styles.planSubtitle}>{detail.subtitle}</Text>

                {selectedPeriod === 'MONTH' ? (
                  <View style={styles.introBadge}>
                    <Text style={styles.introBadgeText}>
                      Первая неделя: {detail.introPrice} € (3 € за занятие)
                    </Text>
                  </View>
                ) : (
                  <View style={styles.annualBreakdown}>
                    {isSpecialAnnual && (plan.annual_savings ?? 0) > 0 ? (
                      <View style={styles.savingsChipSpecial}>
                        <Text style={styles.savingsChipSpecialText}>
                          Экономия {plan.annual_savings} € в первый год
                        </Text>
                      </View>
                    ) : !isSpecialAnnual ? (
                      <Text style={styles.annualBreakdownText}>
                        ≈ {money(Math.round((effectivePrice / 12) * 100) / 100, displayCurrency)} в месяц
                      </Text>
                    ) : null}
                  </View>
                )}

                {/* Payment Details Box — transparent billing summary before checkout */}
                {!isCurrent && !isPending && !(subscription && subscription.status === 'ACTIVE') ? (
                  <View style={styles.paymentDetailsBox}>
                    <Text style={styles.paymentDetailsTitle}>Условия оплаты</Text>
                    {selectedPeriod === 'MONTH' ? (
                      <>
                        <Text style={styles.paymentDetailRow}>
                          🗓 Первая неделя: <Text style={styles.paymentDetailBold}>{detail.introPrice} €</Text>
                          {` (${plan.lessons_per_week} ${plan.lessons_per_week === 1 ? 'занятие' : 'занятия'} × 3 € = ${detail.introPrice} €)`}
                        </Text>
                        <Text style={styles.paymentDetailRow}>
                          📅 Далее: <Text style={styles.paymentDetailBold}>{money(effectivePrice, displayCurrency)} / месяц</Text> автоматически
                        </Text>
                        <Text style={styles.paymentDetailRow}>
                          🔄 Подписка продлевается каждый месяц до отмены
                        </Text>
                        <Text style={styles.paymentDetailRow}>
                          ❌ Отмена: через PayPal или настройки приложения в любое время
                        </Text>
                      </>
                    ) : (
                      <>
                        <Text style={styles.paymentDetailRow}>
                          🗓 Первая неделя: <Text style={styles.paymentDetailBold}>{detail.introPrice} €</Text>
                          {` (${plan.lessons_per_week} ${plan.lessons_per_week === 1 ? 'занятие' : 'занятия'} × 3 € = ${detail.introPrice} €)`}
                        </Text>
                        {isSpecialAnnual ? (
                          <>
                            <Text style={styles.paymentDetailRow}>
                              🌟 Первый год: <Text style={styles.paymentDetailBold}>{money(effectivePrice, displayCurrency)}</Text>
                            </Text>
                            <Text style={styles.paymentDetailRow}>
                              🔄 Следующее продление: <Text style={styles.paymentDetailBold}>{money(plan.standard_renewal_price, displayCurrency)} / год</Text>
                            </Text>
                          </>
                        ) : (
                          <Text style={styles.paymentDetailRow}>
                            📅 Далее: <Text style={styles.paymentDetailBold}>{money(effectivePrice, displayCurrency)} / год</Text> автоматически
                          </Text>
                        )}
                        <Text style={styles.paymentDetailRow}>
                          ❌ Отмена: через PayPal или настройки приложения в любое время
                        </Text>
                      </>
                    )}
                  </View>
                ) : null}

                <Button
                  disabled={busy || Boolean(isCurrent)}
                  secondary={!isCurrent && !isPending}
                  title={
                    isCurrent
                      ? '✓ Текущий тариф'
                      : isPending
                      ? 'Запланирован'
                      : subscription && subscription.status === 'ACTIVE'
                      ? 'Выбрать этот тариф'
                      : selectedPeriod === 'YEAR'
                      ? `Оформить подписку (${money(effectivePrice, displayCurrency)} / год)`
                      : `Оформить подписку (${money(effectivePrice, displayCurrency)})`
                  }
                  onPress={() => handleSelectPlan(plan)}
                />

                {/* Mandatory renewal disclosure for special annual */}
                {isSpecialAnnual && plan.renewal_disclosure ? (
                  <Text style={styles.renewalDisclosure}>{plan.renewal_disclosure}</Text>
                ) : null}

                {/* Legal consent line — shown for new subscriptions only */}
                {!isCurrent && !isPending && !(subscription && subscription.status === 'ACTIVE') ? (
                  <Text style={styles.legalConsentLine}>
                    Оформляя подписку, вы принимаете{' '}
                    <Text
                      style={styles.legalConsentLink}
                      onPress={() => setLegalModalDoc({
                        title: 'Пользовательское соглашение',
                        body: 'DOME предоставляет ограниченное, личное, непередаваемое право доступа к цифровым образовательным материалам на срок и в количестве, указанном при покупке. Каждый выданный урок может быть полностью завершён не более двух раз. Каждое полное прохождение может создавать отдельный персонализированный мультфильм. Домашнее задание выдаётся один раз после первого полного прохождения. Покупка является оплатой цифрового доступа, а не гарантией конкретного образовательного результата. После активации и начала предоставления цифрового контента платежи не возвращаются, кроме случаев, когда возврат прямо обязателен применимым законодательством.',
                      })}
                    >
                      Пользовательское соглашение
                    </Text>
                    {', '}
                    <Text
                      style={styles.legalConsentLink}
                      onPress={() => setLegalModalDoc({
                        title: 'Политику конфиденциальности',
                        body: 'Мы обрабатываем данные родителя и ребёнка только для регистрации, предоставления обучения, сохранения прогресса, безопасности, поддержки, платежей, отчётов и создания персонализированных материалов. Данные ребёнка не продаются и не используются для поведенческой рекламы. Сроки хранения должны быть ограничены необходимостью и требованиями закона. Родитель может запросить доступ, исправление, экспорт или удаление данных в пределах применимого законодательства.',
                      })}
                    >
                      Политику конфиденциальности
                    </Text>
                    {' и '}
                    <Text
                      style={styles.legalConsentLink}
                      onPress={() => setLegalModalDoc({
                        title: 'Условия подписки и автопродления',
                        body: 'Подписка оформляется на выбранный период (месяц или год) и автоматически продлевается до отмены. Первая неделя тарифицируется по 3 € за занятие. Для месячных тарифов следующее списание производится по выбранной месячной цене. Для годовых тарифов — по годовой цене. Вы можете отменить подписку в любое время через настройки PayPal или в профиле приложения. Отмена прекращает автоматическое продление — текущий период остаётся активным до его окончания.',
                      })}
                    >
                      Условия подписки
                    </Text>
                    .
                  </Text>
                ) : null}
              </Card>
            );
          })}
        </View>
      ) : null}

      {/* Plan Change Preview Dialog */}
      {preview ? (
        <Card>
          <H2>Подтверждение изменения</H2>
          <Body>
            Новый тариф начнёт действовать со следующего оплачиваемого периода. До этой даты
            действует ваш текущий тариф.
          </Body>
          <Body>Текущий тариф: {preview.current_plan.title}</Body>
          <Body>Действует до: {date(preview.effective_at)}</Body>
          <Body>Новый тариф: {preview.new_plan.title}</Body>
          <Body>
            Стоимость следующего периода: {money(preview.new_plan.price, preview.new_plan.currency)}{' '}
            за {periodLabel(preview.new_plan.billing_period)}
          </Body>
          <Body>Начнет действовать: {date(preview.effective_at)}</Body>
          <Button disabled={busy} title="Подтвердить изменение тарифа" onPress={confirmChange} />
          <Button disabled={busy} secondary title="Назад к тарифам" onPress={() => setPreview(null)} />
        </Card>
      ) : null}

      <Button disabled={busy} title="Назад" secondary onPress={handleBack} />

      {/* Legal Document Modal */}
      <Modal
        visible={legalModalDoc !== null}
        animationType="slide"
        transparent={false}
        onRequestClose={() => setLegalModalDoc(null)}
      >
        <View style={styles.legalModalContainer}>
          <View style={styles.legalModalHeader}>
            <Text style={styles.legalModalTitle}>{legalModalDoc?.title}</Text>
            <Pressable onPress={() => setLegalModalDoc(null)} style={styles.legalModalClose}>
              <Text style={styles.legalModalCloseText}>✕ Закрыть</Text>
            </Pressable>
          </View>
          <ScrollView style={styles.legalModalBody} contentContainerStyle={{ padding: 20 }}>
            <Text style={styles.legalModalText}>{legalModalDoc?.body}</Text>
          </ScrollView>
        </View>
      </Modal>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  ownerBadge: {
    backgroundColor: '#FEF3C7',
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 8,
    alignSelf: 'flex-start',
    marginBottom: 8,
  },
  ownerBadgeText: {
    color: '#92400E',
    fontWeight: '800',
    fontSize: 12,
    letterSpacing: 0.5,
  },
  toggleContainer: {
    flexDirection: 'row',
    backgroundColor: '#E5E7EB',
    borderRadius: 14,
    padding: 4,
    marginTop: 8,
    marginBottom: 16,
  },
  toggleButton: {
    flex: 1,
    paddingVertical: 10,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: 10,
  },
  toggleButtonActive: {
    backgroundColor: '#FFFFFF',
    shadowColor: '#000',
    shadowOpacity: 0.08,
    shadowRadius: 3,
    shadowOffset: { width: 0, height: 2 },
    elevation: 2,
  },
  toggleButtonText: {
    fontSize: 15,
    fontWeight: '600',
    color: '#6B7280',
  },
  toggleButtonTextActive: {
    color: theme.colors.primary,
    fontWeight: '800',
  },
  savingsTag: {
    backgroundColor: '#10B981',
    borderRadius: 6,
    paddingHorizontal: 6,
    paddingVertical: 2,
  },
  savingsTagText: {
    color: '#FFFFFF',
    fontSize: 11,
    fontWeight: '800',
  },
  introBanner: {
    backgroundColor: '#EFF6FF',
    borderWidth: 1,
    borderColor: '#BFDBFE',
    borderRadius: 12,
    padding: 14,
    marginBottom: 8,
  },
  introBannerTitle: {
    color: '#1E40AF',
    fontWeight: '800',
    fontSize: 14,
    marginBottom: 4,
  },
  introBannerSub: {
    color: '#3B82F6',
    fontSize: 12,
    lineHeight: 17,
  },
  annualBanner: {
    backgroundColor: '#ECFDF5',
    borderWidth: 1,
    borderColor: '#A7F3D0',
    borderRadius: 12,
    padding: 14,
    marginBottom: 8,
  },
  annualBannerTitle: {
    color: '#065F46',
    fontWeight: '800',
    fontSize: 14,
    marginBottom: 4,
  },
  annualBannerSub: {
    color: '#059669',
    fontSize: 12,
    lineHeight: 17,
  },
  planCardHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 4,
  },
  planTitle: {
    fontSize: 18,
    fontWeight: '800',
    color: theme.colors.text,
  },
  planPriceBadge: {
    fontSize: 17,
    fontWeight: '800',
    color: theme.colors.primary,
  },
  planSubtitle: {
    fontSize: 13,
    color: theme.colors.muted,
    marginBottom: 10,
  },
  introBadge: {
    backgroundColor: '#FEF3C7',
    borderRadius: 8,
    paddingHorizontal: 10,
    paddingVertical: 6,
    marginBottom: 10,
    alignSelf: 'flex-start',
  },
  introBadgeText: {
    color: '#B45309',
    fontWeight: '700',
    fontSize: 12,
  },
  annualBreakdown: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    marginBottom: 10,
  },
  annualBreakdownText: {
    fontSize: 13,
    fontWeight: '600',
    color: '#4B5563',
  },
  savingsChip: {
    backgroundColor: '#D1FAE5',
    borderRadius: 6,
    paddingHorizontal: 8,
    paddingVertical: 3,
  },
  savingsChipText: {
    color: '#065F46',
    fontWeight: '700',
    fontSize: 11,
  },
  // Special annual pricing styles
  annualBannerSpecial: {
    backgroundColor: '#FFF7ED',
    borderWidth: 1,
    borderColor: '#FED7AA',
    borderRadius: 12,
    padding: 14,
    marginBottom: 8,
  },
  annualBannerTitleSpecial: {
    color: '#92400E',
    fontWeight: '800',
    fontSize: 14,
    marginBottom: 4,
  },
  annualBannerSubSpecial: {
    color: '#B45309',
    fontSize: 12,
    lineHeight: 17,
  },
  specialBadge: {
    backgroundColor: '#FEF3C7',
    borderRadius: 8,
    paddingHorizontal: 10,
    paddingVertical: 4,
    alignSelf: 'flex-start',
    marginBottom: 8,
  },
  specialBadgeText: {
    color: '#92400E',
    fontWeight: '800',
    fontSize: 11,
    letterSpacing: 0.3,
  },
  annualBadge: {
    backgroundColor: '#ECFDF5',
    borderRadius: 8,
    paddingHorizontal: 10,
    paddingVertical: 4,
    alignSelf: 'flex-start',
    marginBottom: 8,
  },
  annualBadgeText: {
    color: '#065F46',
    fontWeight: '700',
    fontSize: 11,
  },
  planPriceStrikethrough: {
    fontSize: 13,
    fontWeight: '600',
    color: '#9CA3AF',
    textDecorationLine: 'line-through',
  },
  planPriceSpecial: {
    fontSize: 18,
    fontWeight: '800',
    color: '#D97706',
  },
  savingsChipSpecial: {
    backgroundColor: '#FEF3C7',
    borderRadius: 6,
    paddingHorizontal: 8,
    paddingVertical: 3,
  },
  savingsChipSpecialText: {
    color: '#92400E',
    fontWeight: '700',
    fontSize: 11,
  },
  renewalDisclosure: {
    fontSize: 11,
    color: '#6B7280',
    lineHeight: 16,
    marginTop: 8,
    fontStyle: 'italic',
  },
  // Payment details box — transparent billing summary before checkout button
  paymentDetailsBox: {
    backgroundColor: '#F0F9FF',
    borderWidth: 1,
    borderColor: '#BAE6FD',
    borderRadius: 10,
    padding: 12,
    marginTop: 12,
    marginBottom: 4,
    gap: 4,
  },
  paymentDetailsTitle: {
    fontSize: 12,
    fontWeight: '700',
    color: '#0369A1',
    marginBottom: 6,
    textTransform: 'uppercase',
    letterSpacing: 0.4,
  },
  paymentDetailRow: {
    fontSize: 12,
    color: '#374151',
    lineHeight: 18,
  },
  paymentDetailBold: {
    fontWeight: '700',
    color: '#111827',
  },
  // Legal consent line — shown after checkout button
  legalConsentLine: {
    fontSize: 11,
    color: '#6B7280',
    lineHeight: 16,
    marginTop: 10,
    textAlign: 'center',
  },
  legalConsentLink: {
    color: '#2563EB',
    textDecorationLine: 'underline',
    fontWeight: '500',
  },
  // Legal document modal
  legalModalContainer: {
    flex: 1,
    backgroundColor: '#FFFFFF',
  },
  legalModalHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 20,
    paddingTop: 56,
    paddingBottom: 16,
    borderBottomWidth: 1,
    borderBottomColor: '#E5E7EB',
    backgroundColor: '#F9FAFB',
  },
  legalModalTitle: {
    fontSize: 16,
    fontWeight: '700',
    color: '#111827',
    flex: 1,
    marginRight: 12,
  },
  legalModalClose: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    backgroundColor: '#E5E7EB',
    borderRadius: 8,
  },
  legalModalCloseText: {
    fontSize: 13,
    fontWeight: '600',
    color: '#374151',
  },
  legalModalBody: {
    flex: 1,
  },
  legalModalText: {
    fontSize: 14,
    color: '#374151',
    lineHeight: 22,
  },
});

