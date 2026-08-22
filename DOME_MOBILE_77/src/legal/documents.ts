import { ConsentDocument,UiLanguage } from '../types/domain';

const V='2026-08-20.1';
const hash=(name:string,lang:string)=>`sha256:${name}:${V}:${lang}`; // server replaces with real SHA-256 of immutable document text

const docs:any={
ru:{
 terms:`DOME предоставляет ограниченное, личное, непередаваемое право доступа к цифровым образовательным материалам на срок и в количестве, указанном при покупке. Каждый выданный урок может быть полностью завершён не более двух раз. Каждое полное прохождение может создавать отдельный персонализированный мультфильм. Домашнее задание выдаётся один раз после первого полного прохождения. Покупка является оплатой цифрового доступа, а не гарантией конкретного образовательного результата. После активации и начала предоставления цифрового контента платежи не возвращаются, кроме случаев, когда возврат прямо обязателен применимым законодательством.`,
 privacy:`Мы обрабатываем данные родителя и ребёнка только для регистрации, предоставления обучения, сохранения прогресса, безопасности, поддержки, платежей, отчётов и создания персонализированных материалов. Данные ребёнка не продаются и не используются для поведенческой рекламы. Сроки хранения должны быть ограничены необходимостью и требованиями закона. Родитель может запросить доступ, исправление, экспорт или удаление данных в пределах применимого законодательства.`,
 authority:`Я подтверждаю, что мне 18 лет или больше и я являюсь родителем/законным представителем ребёнка либо имею законные полномочия давать согласия от его имени. Я подтверждаю достоверность предоставленных данных.`,
 voice:`Я разрешаю записывать и обрабатывать голос ребёнка во время учебных заданий для распознавания речи, проверки ответа, голосового взаимодействия, создания персонализированного мультфильма и технического обеспечения сервиса. Я понимаю, что голос может являться персональными данными ребёнка и может обрабатываться уполномоченными технологическими поставщиками по договору.`,
 movie:`Я разрешаю использовать реплики ребёнка, записанные во время конкретного урока, для создания персонализированного мультфильма и доставки его мне. Мультфильм не разрешается использовать публично или в рекламе без отдельного согласия.`,
 recurring:`Я подтверждаю выбранный пакет, цену, валюту и автоматическое ежемесячное продление до отмены. Перед подтверждением оплаты приложение показывает точную сумму и количество новых уроков.`,
 digital:`Я прошу предоставить цифровой доступ сразу после подтверждения оплаты и понимаю, что в юрисдикциях, где это допускается законом, начало предоставления цифрового контента может ограничить или прекратить право на отказ. Обязательные права потребителя сохраняются.`
},
en:{
 terms:`DOME grants a limited, personal, non-transferable right to access digital educational content for the duration and quantity shown at purchase. Each unlocked lesson may be fully completed no more than twice. Each full completion may generate a separate personalized movie. Homework is issued once after the first full completion. The purchase pays for digital access, not a guaranteed educational outcome. After activation and commencement of digital content, payments are non-refundable except where a refund is mandatorily required by applicable law.`,
 privacy:`We process parent and child data only to register users, provide learning, preserve progress, maintain safety, support payments and support, deliver reports, and create personalized materials. Child data is not sold or used for behavioral advertising. Retention must be limited to what is necessary and legally required. Parents may request access, correction, export or deletion subject to applicable law.`,
 authority:`I confirm that I am at least 18 and am the child's parent/legal guardian or otherwise legally authorized to provide consent on the child's behalf. I confirm that the information I provide is accurate.`,
 voice:`I authorize recording and processing the child's voice during learning activities for speech recognition, answer evaluation, voice interaction, creation of personalized movies, and technical operation of the service. I understand that a child's voice may be personal information and may be processed by authorized technology providers under contract.`,
 movie:`I authorize the child's lesson recordings to be used to create a personalized movie and deliver it to me. The movie may not be used publicly or for advertising without separate consent.`,
 recurring:`I confirm the selected package, price, currency, and automatic monthly renewal until cancellation. The app displays the exact charge and lesson quantity before payment confirmation.`,
 digital:`I request immediate digital access after confirmed payment and understand that, where permitted by law, commencement of digital content may limit or end a statutory withdrawal right. Mandatory consumer rights remain unaffected.`
},
de:{
 terms:`DOME gewährt ein begrenztes, persönliches und nicht übertragbares Zugangsrecht zu digitalen Lerninhalten für die beim Kauf angegebene Dauer und Menge. Jede freigeschaltete Lektion kann höchstens zweimal vollständig abgeschlossen werden. Jeder vollständige Durchlauf kann einen eigenen personalisierten Film erzeugen. Hausaufgaben werden einmal nach dem ersten vollständigen Durchlauf ausgegeben. Die Zahlung betrifft digitalen Zugang und garantiert keinen bestimmten Lernerfolg. Rückerstattungen erfolgen nach Aktivierung und Beginn der digitalen Leistung nur, soweit zwingendes Recht dies verlangt.`,
 privacy:`Wir verarbeiten Daten von Eltern und Kindern nur für Registrierung, Unterricht, Fortschritt, Sicherheit, Zahlungen, Support, Berichte und personalisierte Materialien. Kinderdaten werden nicht verkauft und nicht für verhaltensbasierte Werbung genutzt.`,
 authority:`Ich bestätige, dass ich mindestens 18 Jahre alt und Elternteil/gesetzlicher Vertreter des Kindes oder anderweitig rechtlich zur Einwilligung befugt bin.`,
 voice:`Ich erlaube die Aufnahme und Verarbeitung der Stimme des Kindes für Spracherkennung, Antwortbewertung, Sprachinteraktion, personalisierte Filme und den technischen Betrieb.`,
 movie:`Ich erlaube die Verwendung der im Unterricht aufgenommenen Äußerungen des Kindes zur Erstellung und Zustellung eines personalisierten Films. Eine öffentliche oder werbliche Nutzung erfordert eine gesonderte Einwilligung.`,
 recurring:`Ich bestätige Paket, Preis, Währung und automatische monatliche Verlängerung bis zur Kündigung.`,
 digital:`Ich verlange den sofortigen digitalen Zugang nach bestätigter Zahlung und verstehe, dass der Beginn digitaler Inhalte, soweit gesetzlich zulässig, das Widerrufsrecht einschränken oder beenden kann.`
}}
export function legalDocuments(lang:UiLanguage):ConsentDocument[]{const x=docs[lang]||docs.en;return [
 {type:'terms',version:V,language:lang,title:'Terms of Use',bodyMarkdown:x.terms,required:true,hash:hash('terms',lang)},
 {type:'privacy',version:V,language:lang,title:'Privacy Policy',bodyMarkdown:x.privacy,required:true,hash:hash('privacy',lang)},
 {type:'parent_authority',version:V,language:lang,title:'Parent / Guardian confirmation',bodyMarkdown:x.authority,required:true,hash:hash('authority',lang)},
 {type:'voice_ai',version:V,language:lang,title:'Child Voice & AI Consent',bodyMarkdown:x.voice,required:true,hash:hash('voice',lang)},
 {type:'movie',version:V,language:lang,title:'Personalized Movie Consent',bodyMarkdown:x.movie,required:true,hash:hash('movie',lang)},
 {type:'subscription_recurring',version:V,language:lang,title:'Subscription & Recurring Billing',bodyMarkdown:x.recurring,required:true,hash:hash('recurring',lang)},
 {type:'digital_content_start',version:V,language:lang,title:'Immediate Digital Access',bodyMarkdown:x.digital,required:true,hash:hash('digital',lang)}
]}
