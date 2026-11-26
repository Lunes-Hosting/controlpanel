"""Tickets written in another language are shown to staff in English, and staff replies
are shown to the opener in their language once they have written in it."""

import asyncio
import re
from time import perf_counter, sleep
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import discord

from runtime import translation
from runtime.translation import Reading, Reply, interpret, translate_reply
from tests.test_credit_transactions import ROOT
from tests.test_ticket_account_approval import load_code

LOG = ('[12:00:02] [Server thread/ERROR]: Failed to bind to port\n'
       'java.net.BindException: Address already in use\n'
       '\tat sun.nio.ch.Net.bind0(Native Method)')


class Engine:
    """Stands in for the translation service: knows a few phrases, returns anything else unchanged."""

    def __init__(self):
        self.phrases = {
            ('hola, mi servidor no funciona', 'auto'): ('Hello, my server is not working', 'es'),
            ('mi servidor no funciona', 'auto'): ('my server is not working', 'es'),
            ('ayuda por favor', 'auto'): ('help please', 'es'),
            ('ayuda por favor no se que hacer', 'auto'): ("help please I don't know what to do", 'es'),
            ('gimana cara fix nya', 'auto'): ('how to fix it', 'id'),
            ('hola', 'auto'): ('hello', 'es'),
            ('si', 'auto'): ('and', 'ro'),
            ('si', 'es'): ('yes', 'es'),
            ('gracias', 'es'): ('thank you', 'es'),
            ('tysm', 'auto'): ('tysm', 'hi'),
            ('hru', 'auto'): ('the game', 'cs'),
            ('我的伺服器無法啟動', 'auto'): ('My server cannot start', 'en'),
            ('请帮帮我', 'auto'): ('please help me', 'zh-CN'),
            ('teh bot', 'auto'): ('bot tea', 'id'),
            ('pliz halp', 'auto'): ('please please', 'hi'),
            ('我每天都在用，为什么会这样？', 'auto'): ('I use it every day, why is this happening?', 'zh-CN'),
            ('ejecuté <@0> y me sale este error en <@1> no sé qué hacer', 'auto'):
                ("I ran <@0> and I get this error on <@1> I don't know what to do", 'es'),
            ('o servidor não liga desde ontem', 'auto'): ('the server has not turned on since yesterday', 'pt'),
            ('what is the issue', 'en'): ('cual es el problema', 'en'),
            ('Hello <@0>, open <@1> and run <@2>', 'en'): ('Hola <@0>, abre <@1> y ejecuta <@2>', 'en'),
            ('Then run <@0> again', 'en'): ('Luego ejecuta de nuevo', 'en'),
            ('you need to renew your server right now please', 'en'):
                ('necesitas renovar tu servidor ahora mismo por favor', 'en'),
            ('then restart it', 'en'): ('luego reinícialo', 'en'),
            ('Automatic translation', 'en'): ('Traducción automática', 'en'),
        }
        # English translated back into the language it came from, keyed by that language.
        self.back = {
            ('please help', 'de'): 'Bitte helfen Sie',
            ('server down', 'es'): 'servidor caído',
            ('please please', 'hi'): 'कृपया कृपया',
            ('price of money', 'yo'): 'owo ti owo',
        }
        self.batches = []
        self.down = False

    def translate_batch(self, texts, target, source='auto'):
        self.batches.append((list(texts), target, source))
        if self.down:
            return None
        return [(self.back[text, target], source) if (text, target) in self.back else
                self.phrases.get((text, source), (text, 'en' if source == 'auto' else source)) for text in texts]


class TranslationCase(unittest.TestCase):
    def setUp(self):
        self.engine = Engine()
        patcher = patch.object(translation, 'translate_batch', self.engine.translate_batch)
        patcher.start()
        self.addCleanup(patcher.stop)

    def sent(self):
        return [text for texts, _, _ in self.engine.batches for text in texts]


class OpenerMessageTests(TranslationCase):
    def test_english_messages_are_left_alone_without_asking_the_service(self):
        for text in ('my server is not working', 'hi i need help with my server it keeps crashing', 'ok thanks',
                     'can u help me pls', '?', 'tysm bro', 'https://example.com/servers/1 this one', ''):
            self.assertIsNone(interpret(text), text)
        self.assertEqual(self.engine.batches, [])

    def test_a_message_in_another_language_is_read_in_english(self):
        self.assertEqual(interpret('hola, mi servidor no funciona'),
                         Reading('es', 'Hello, my server is not working'))
        self.assertEqual(len(self.engine.batches), 1)

    def test_a_pasted_error_alone_says_nothing_about_the_language(self):
        self.assertIsNone(interpret(LOG))
        self.assertIsNone(interpret("Error: Cannot find module 'discord.js'"))
        self.assertIsNone(interpret('```\nnpm ERR! code ELIFECYCLE\n```'))
        self.assertEqual(self.engine.batches, [])

    def test_the_person_s_own_words_decide_the_language_not_what_they_pasted(self):
        reading = interpret(LOG + '\nayuda por favor no se que hacer')
        self.assertEqual(reading, Reading('es', "help please I don't know what to do"))
        # The log is neither sent for translation nor repeated in the English version.
        self.assertEqual(self.sent(), ['ayuda por favor no se que hacer'])

    def test_english_copied_from_the_site_stays_as_it_is(self):
        reading = interpret('Your server has been suspended due to inactivity.\n我每天都在用，为什么会这样？')
        self.assertEqual(reading.language, 'zh-CN')
        self.assertEqual(reading.english, 'Your server has been suspended due to inactivity.\n'
                                          'I use it every day, why is this happening?')
        self.assertEqual(self.sent(), ['我每天都在用，为什么会这样？'])

    def test_another_language_at_the_end_of_an_english_line_is_found(self):
        self.assertEqual(interpret('my server is not working ayuda por favor'),
                         Reading('es', 'my server is not working help please'))

    def test_own_words_after_an_error_on_the_same_line_are_found(self):
        self.assertEqual(interpret("Error: Cannot find module 'discord.js' gimana cara fix nya"),
                         Reading('id', "Error: Cannot find module 'discord.js' how to fix it"))

    def test_commands_and_links_survive_translation(self):
        reading = interpret('ejecuté `npm install` y me sale este error en https://example.com/servers no sé qué hacer')
        self.assertEqual(reading.english, "I ran `npm install` and I get this error on https://example.com/servers "
                                          "I don't know what to do")

    def test_keyboard_mashing_and_slang_are_not_a_language(self):
        # The service "translates" some of these and names a language for them.
        self.engine.phrases.update({('jajaja', 'auto'): ('ha ha ha', 'es'), ('kkkk', 'auto'): ('hahahaha', 'pt'),
                                    ('zxcvbnm lkjhgf', 'auto'): ('something else', 'sk')})
        self.engine.back['something else', 'sk'] = 'zxcvbnm lkjhgf'
        texts = ('asdfgh qwerty zxcvb', 'jkhasdkjh askdjh', 'aaaaaaa', 'tysm', 'hru', 'wsg gng', 'jajaja', 'kkkk',
                 'zxcvbnm lkjhgf')
        for text in texts:
            self.assertIsNone(interpret(text), text)
        # Each was asked about once at most: none looked enough like a language to check further.
        self.assertLessEqual(len(self.engine.batches), len(texts))

    def test_a_single_word_is_too_little_to_go_on(self):
        # "si" is "yes" in Spanish and "and" in Romanian; "teh" is a typo and also Indonesian for tea.
        self.engine.back.update({('hello', 'es'): 'Hola', ('and', 'ro'): 'şi', ('bot tea', 'id'): 'teh bot'})
        for text in ('hola', 'si', 'teh bot'):
            self.assertIsNone(interpret(text), text)
        self.assertEqual([texts for texts, _, _ in self.engine.batches], [['hola'], ['si'], ['teh bot']])

    def test_two_words_count_when_translating_them_back_returns_the_same_words(self):
        self.engine.phrases.update({('bitte helfen', 'auto'): ('please help', 'de'),
                                    ('servidor caido', 'auto'): ('server down', 'es')})
        self.assertEqual(interpret('bitte helfen'), Reading('de', 'please help'))
        self.assertEqual(interpret('servidor caido'), Reading('es', 'server down'))  # accents may differ
        self.assertEqual(self.engine.batches[1], (['please help'], 'de', 'en'))

    def test_two_misspelled_or_slang_words_do_not_come_back_as_themselves(self):
        self.engine.phrases['uwu owo', 'auto'] = ('price of money', 'yo')
        self.assertIsNone(interpret('pliz halp'))
        self.assertIsNone(interpret('uwu owo'))
        self.engine.down = True  # and nothing is assumed when the check cannot be made
        self.assertIsNone(interpret('bitte helfen'))

    def test_a_short_message_in_another_alphabet_is_enough(self):
        self.assertEqual(interpret('I get this error when I start the server. 请帮帮我'),
                         Reading('zh-CN', 'I get this error when I start the server. please help me'))
        self.assertEqual(len(self.engine.batches), 1)

    def test_a_typo_in_a_translated_ticket_is_not_passed_off_as_a_translation(self):
        self.assertIsNone(interpret('teh bot', known='es'))

    def test_short_lines_are_read_in_the_language_of_the_rest_of_the_message(self):
        reading = interpret('mi servidor no funciona\nsi\ngracias')
        self.assertEqual(reading, Reading('es', 'my server is not working\nyes\nthank you'))
        self.assertEqual(self.engine.batches[-1], (['gracias', 'si'], 'en', 'es'))

    def test_the_ticket_language_is_kept_for_short_messages(self):
        self.assertEqual(interpret('si', known='es'), Reading('es', 'yes'))

    def test_the_ticket_language_changes_when_the_opener_clearly_writes_another(self):
        self.assertEqual(interpret('o servidor não liga desde ontem', known='es').language, 'pt')
        self.assertEqual(interpret('ayuda por favor', known='pt').language, 'es')

    def test_traditional_chinese_reported_as_english_is_still_recognised(self):
        self.assertEqual(interpret('我的伺服器無法啟動'), Reading('zh-TW', 'My server cannot start'))

    def test_nothing_happens_when_the_service_does_not_answer(self):
        self.engine.down = True
        self.assertIsNone(interpret('hola, mi servidor no funciona'))

    def test_a_huge_paste_is_sent_in_one_bounded_request(self):
        interpret('\n'.join(f'linea numero {index} de mi mensaje' for index in range(500)))
        self.assertEqual(len(self.engine.batches), 1)
        self.assertEqual(len(self.engine.batches[0][0]), translation.MAX_ITEMS)

    def test_of_a_huge_paste_the_start_and_the_end_are_read(self):
        filler = 'my server is not working\n' * 200
        reading = interpret('hola, mi servidor no funciona\n' + filler + 'ayuda por favor no se que hacer\n' + filler
                            + 'mi servidor no funciona')
        self.assertEqual(reading.language, 'es')
        self.assertIn('hola, mi servidor no funciona', self.sent())
        self.assertIn('mi servidor no funciona', self.sent())
        self.assertNotIn('ayuda por favor no se que hacer', self.sent())
        # An unfinished code block at the cut does not swallow the person's words after it.
        self.assertEqual(interpret('```\n' + 'x = 1\n' * 2000 + 'mi servidor no funciona').language, 'es')

    def test_a_huge_hostile_message_cannot_stall_the_panel(self):
        # Each of these once took time growing with the square of the length: a minute or more here.
        for unit in ('a-', 'a', '.', 'a@', 'a.b-c_d/e@f'):
            started = perf_counter()
            interpret(unit * 30000)
            translate_reply(unit * 30000, 'es')
            self.assertLess(perf_counter() - started, 3, unit)


class StaffReplyTests(TranslationCase):
    def test_a_reply_is_translated_from_english_with_a_label_in_that_language(self):
        self.assertEqual(translate_reply('what is the issue', 'es'),
                         Reply('cual es el problema', 'Traducción automática'))
        self.assertEqual(self.engine.batches, [(['what is the issue', 'Automatic translation'], 'es', 'en')])

    def test_mentions_links_and_commands_are_kept_exactly(self):
        reply = translate_reply('Hello <@123456789>, open https://example.com/servers and run `npm install`', 'es')
        self.assertEqual(reply.text, 'Hola <@123456789>, abre https://example.com/servers y ejecuta `npm install`')

    def test_protected_text_the_service_drops_is_put_back(self):
        self.assertEqual(translate_reply('Then run `npm install` again', 'es').text,
                         'Luego ejecuta de nuevo `npm install`')

    def test_chat_shorthand_is_spelled_out_before_translating(self):
        self.assertEqual(translate_reply('u need to renew ur server rn pls', 'es').text,
                         'necesitas renovar tu servidor ahora mismo por favor')

    def test_code_blocks_are_not_translated(self):
        reply = translate_reply('```\ncd /home/container\nnpm install\n```\nthen restart it', 'es')
        self.assertEqual(reply.text, '```\ncd /home/container\nnpm install\n```\nluego reinícialo')
        self.assertEqual(self.engine.batches[0][0], ['then restart it', 'Automatic translation'])

    def test_messages_with_nothing_to_translate_are_skipped(self):
        for text in ('?', '...', 'https://example.com', '<@123456789>', '```\nnpm install\n```', '', '12345'):
            self.assertIsNone(translate_reply(text, 'es'), text)
        self.assertEqual(self.engine.batches, [])

    def test_commands_for_other_bots_are_skipped(self):
        for text in ('$close', '!rename new-name', '-transcript', '/close resolved'):
            self.assertIsNone(translate_reply(text, 'es'), text)
        self.assertEqual(self.engine.batches, [])
        # A sentence that merely starts with a symbol is still a reply.
        translate_reply('.env file is missing from your server', 'es')
        self.assertEqual(len(self.engine.batches), 1)

    def test_a_reply_that_comes_back_unchanged_is_not_repeated(self):
        # "ok", or a reply staff already wrote in the opener's language.
        self.assertIsNone(translate_reply('ok', 'es'))
        self.assertIsNone(translate_reply('hola, ya revisé tu servidor', 'es'))

    def test_no_reply_without_a_usable_language_or_service(self):
        for language in (None, '', 'en', 'not a language', 5):
            self.assertIsNone(translate_reply('what is the issue', language), language)
        self.engine.down = True
        self.assertIsNone(translate_reply('what is the issue', 'es'))


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.reset()
        self.addCleanup(self.reset)
        self.requests = []
        self.answers = {}
        self.now = 1000.0
        self.warning = Mock()
        for target, name, fake in ((translation.requests, 'post', self.post), (translation, 'monotonic', lambda: self.now),
                                   (translation.log, 'warning', self.warning)):
            patcher = patch.object(target, name, fake)
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def reset():
        translation._paused_until.clear()
        translation._recent.clear()

    def post(self, url, params=None, data=None, headers=None, timeout=None):
        self.requests.append((url, dict(params), data))
        status, body = self.answers.get(url, (200, None))
        if isinstance(body, Exception):
            raise body
        return SimpleNamespace(status_code=status, json=lambda: body)

    def test_each_text_comes_back_with_its_own_language(self):
        first = translation._BATCH_ENDPOINTS[0][0]
        self.answers[first] = (200, [['Hello', 'es'], ['my server', 'en']])
        self.assertEqual(translation.translate_batch(['hola', 'my server'], 'en'), [('Hello', 'es'), ('my server', 'en')])
        url, params, data = self.requests[0]
        self.assertEqual((url, params['sl'], params['tl'], data), (first, 'auto', 'en', [('q', 'hola'), ('q', 'my server')]))

    def test_a_fixed_source_language_answers_with_plain_text(self):
        self.answers[translation._BATCH_ENDPOINTS[0][0]] = (200, ['cual es el problema'])
        self.assertEqual(translation.translate_batch(['what is the issue'], 'es', 'en'), [('cual es el problema', 'en')])

    def test_a_refusing_endpoint_is_left_alone_and_the_next_one_is_used(self):
        first, second = translation._BATCH_ENDPOINTS[0][0], translation._BATCH_ENDPOINTS[1][0]
        self.answers[first] = (429, None)
        self.answers[second] = (200, [['Hello', 'es']])
        for _ in range(3):
            self.assertEqual(translation.translate_batch(['hola'], 'en'), [('Hello', 'es')])
        self.assertEqual([url for url, _, _ in self.requests], [first, second, second, second])
        # The bot's log says why, once, so a missing translation can be traced.
        self.warning.assert_called_once()
        self.assertEqual(self.warning.call_args.args[1:], ('translate.googleapis.com', 'HTTP 429', 900))

    def test_nothing_is_returned_when_every_endpoint_fails_or_answers_nonsense(self):
        for answers in ([(500, None), (200, {'unexpected': True}), (200, [['only one']])],
                        [(200, [None, 5]), (200, 'text'), (200, [['a', 'es'], ['b', 'es'], ['c', 'es']])]):
            translation._paused_until.clear()
            for (url, _), answer in zip(translation._BATCH_ENDPOINTS, answers):
                self.answers[url] = answer
            self.assertIsNone(translation.translate_batch(['hola', 'mundo'], 'en'))
        self.answers = {url: (200, translation.requests.ConnectionError('offline'))
                        for url, _ in translation._BATCH_ENDPOINTS}
        translation._paused_until.clear()
        self.assertIsNone(translation.translate_batch(['hola'], 'en'))

    def test_a_refusing_endpoint_is_tried_again_later(self):
        first, second = translation._BATCH_ENDPOINTS[0][0], translation._BATCH_ENDPOINTS[1][0]
        self.answers[first] = (429, None)
        self.answers[second] = (200, [['Hello', 'es']])
        translation.translate_batch(['hola'], 'en')
        self.answers[first] = (200, [['Hello', 'es']])
        self.now += 899
        translation.translate_batch(['hola'], 'en')
        self.now += 2
        translation.translate_batch(['hola'], 'en')
        self.assertEqual([url for url, _, _ in self.requests], [first, second, second, first])

    def test_a_flood_of_messages_does_not_use_up_the_free_service(self):
        self.answers[translation._BATCH_ENDPOINTS[0][0]] = (200, [['Hello', 'es']])
        for _ in range(translation.PER_MINUTE):
            self.assertIsNotNone(translation.translate_batch(['hola'], 'en'))
        self.assertIsNone(translation.translate_batch(['hola'], 'en'))
        self.assertEqual(len(self.requests), translation.PER_MINUTE)
        self.now += 61
        self.assertIsNotNone(translation.translate_batch(['hola'], 'en'))


def text_channel(name='ticket-0001', category='Tickets', public=False, channel_id=500, category_id=50):
    channel = Mock(spec=discord.TextChannel)
    channel.id = channel_id
    channel.name = name
    channel.category = SimpleNamespace(id=category_id, name=category)
    channel.guild = SimpleNamespace(id=1, default_role='everyone', get_member=Mock(return_value=None))
    channel.permissions_for.return_value = SimpleNamespace(view_channel=public)
    channel.send = AsyncMock()
    return channel


class DiscordCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.languages = {}
        self.interpret = Mock(return_value=None)
        self.translate_reply = Mock(return_value=None)
        self.ns = load_code('discord_bot/ticket_translation.py', dict(
            asyncio=asyncio, discord=discord, logger=Mock(), ENABLED=True, DISCORD_GUILD_ID=1, TRANSLATION_WAIT=10,
            DISCORD_TICKET_CATEGORY_IDS={77}, _MENTION=re.compile(r'<@!?(\d+)>'), _openers={},
            get_ticket_id=Mock(return_value=None), get_ticket_language=self.languages.get,
            set_ticket_language=self.languages.__setitem__, interpret=self.interpret,
            translate_reply=self.translate_reply, language_name=translation.language_name,
            is_staff_member=lambda member: 1024761808428466257 in [role.id for role in member.roles]))
        self.channel = text_channel()


class TranslationMessageTests(DiscordCase):
    async def test_the_english_version_is_posted_and_the_language_remembered(self):
        self.interpret.return_value = Reading('es', 'Hello, my server is not working')
        await self.ns['show_english'](self.channel, 'ticket-1', 'hola, mi servidor no funciona')
        embed = self.channel.send.await_args.kwargs['embed']
        self.assertEqual(embed.author.name, 'Translated from Spanish')
        self.assertEqual(embed.description, 'Hello, my server is not working')
        self.assertEqual(embed.footer.text, 'Staff replies in this ticket are now translated to Spanish.')
        self.assertEqual(self.languages, {'ticket-1': 'es'})
        self.interpret.assert_called_once_with('hola, mi servidor no funciona', None)

    async def test_later_messages_are_translated_without_repeating_the_notice(self):
        self.languages['ticket-1'] = 'es'
        self.interpret.return_value = Reading('es', 'thank you')
        await self.ns['show_english'](self.channel, 'ticket-1', 'gracias')
        self.assertFalse(self.channel.send.await_args.kwargs['embed'].footer)
        self.interpret.assert_called_once_with('gracias', 'es')

    async def test_english_gibberish_and_pasted_errors_post_nothing(self):
        await self.ns['show_english'](self.channel, 'ticket-1', 'my server is not working')
        self.channel.send.assert_not_awaited()
        self.assertEqual(self.languages, {})

    async def test_the_translation_never_pings_anyone(self):
        self.interpret.return_value = Reading('es', '@everyone help <@5>')
        message = SimpleNamespace(reply=AsyncMock())
        await self.ns['show_english'](self.channel, '500', '@everyone ayuda <@5>', reply_to=message)
        sent = message.reply.await_args.kwargs
        self.assertFalse(sent['mention_author'])
        self.assertEqual(sent['allowed_mentions'].to_dict(), discord.AllowedMentions.none().to_dict())
        self.channel.send.assert_not_awaited()

    async def test_the_translation_is_posted_on_its_own_when_it_cannot_be_a_reply(self):
        self.interpret.return_value = Reading('es', 'help please')
        gone = discord.NotFound(SimpleNamespace(status=404, reason='Not Found'), 'Unknown message')
        await self.ns['show_english'](self.channel, '500', 'ayuda por favor', reply_to=SimpleNamespace(reply=AsyncMock(side_effect=gone)))
        self.assertEqual(self.channel.send.await_args.kwargs['embed'].description, 'help please')
        self.assertEqual(self.languages, {'500': 'es'})

    REPLY = Reply('cual es el problema', 'Traducción automática')

    async def test_staff_are_not_translated_until_the_opener_writes_in_another_language(self):
        self.assertIsNone(await self.ns['translate_for_opener']('ticket-1', 'what is the issue'))
        self.translate_reply.assert_not_called()

    async def test_a_staff_reply_is_translated_into_the_opener_s_language(self):
        self.languages['500'] = 'es'
        self.translate_reply.return_value = self.REPLY
        self.assertEqual(await self.ns['translate_for_opener']('500', 'what is the issue'), (self.REPLY, 'es'))
        self.translate_reply.assert_called_once_with('what is the issue', 'es')
        self.translate_reply.return_value = None  # nothing to translate, such as "?"
        self.assertIsNone(await self.ns['translate_for_opener']('500', '?'))

    async def test_in_a_discord_ticket_the_translation_is_posted_with_a_label_in_that_language(self):
        message = SimpleNamespace(reply=AsyncMock())
        await self.ns['show_translation'](self.channel, self.REPLY, 'es', reply_to=message)
        embed = message.reply.await_args.kwargs['embed']
        self.assertEqual((embed.description, embed.footer.text), ('cual es el problema', 'Traducción automática'))

    async def test_in_a_web_ticket_s_channel_staff_are_told_what_the_website_shows(self):
        await self.ns['show_translation'](self.channel, self.REPLY, 'es', web=True)
        embed = self.channel.send.await_args.kwargs['embed']
        self.assertEqual((embed.author.name, embed.description), ('Sent to the web ticket in Spanish', 'cual es el problema'))
        self.assertEqual(self.ns['web_reply'](self.REPLY), '[Traducción automática] cual es el problema')
        # Discord refusing the copy must not undo what the website already shows.
        self.channel.send.side_effect = RuntimeError('missing permissions')
        await self.ns['show_translation'](self.channel, self.REPLY, 'es', web=True)

    async def test_a_reply_is_not_held_up_by_a_slow_translation(self):
        self.languages['ticket-1'] = 'es'
        self.ns['TRANSLATION_WAIT'] = 0.05
        self.translate_reply.side_effect = lambda text, language: sleep(0.4) or self.REPLY
        started = perf_counter()
        self.assertIsNone(await self.ns['translate_for_opener']('ticket-1', 'what is the issue'))
        self.assertLess(perf_counter() - started, 0.3)

    async def test_a_failure_never_reaches_the_ticket_flow(self):
        self.interpret.side_effect = RuntimeError('boom')
        self.translate_reply.side_effect = RuntimeError('boom')
        self.languages['ticket-1'] = 'es'
        await self.ns['show_english'](self.channel, 'ticket-1', 'hola')
        self.assertIsNone(await self.ns['translate_for_opener']('ticket-1', 'hi'))
        self.channel.send.assert_not_awaited()

    async def test_switched_off_does_nothing(self):
        self.ns['ENABLED'] = False
        self.languages['ticket-1'] = 'es'
        await self.ns['show_english'](self.channel, 'ticket-1', 'hola')
        self.assertIsNone(await self.ns['translate_for_opener']('ticket-1', 'hi'))
        await self.ns['translate_discord_ticket_message'](self.message('hola'))
        self.interpret.assert_not_called()
        self.translate_reply.assert_not_called()

    def message(self, content, roles=(), author_id=9, bot=False, guild_id=1):
        author = SimpleNamespace(id=author_id, bot=bot, roles=[SimpleNamespace(id=role) for role in roles])
        return SimpleNamespace(content=content, author=author, channel=self.channel,
                               guild=SimpleNamespace(id=guild_id), reply=AsyncMock())


class DiscordTicketTests(DiscordCase):
    STAFF = 1024761808428466257

    def setUp(self):
        super().setUp()
        self.ns['show_english'] = AsyncMock()
        self.ns['translate_for_opener'] = AsyncMock(return_value=(TranslationMessageTests.REPLY, 'es'))
        self.ns['show_translation'] = AsyncMock()
        self.first_message = SimpleNamespace(author=SimpleNamespace(bot=True), content='<@9> Welcome', embeds=[])
        self.channel.history = Mock(side_effect=lambda **options: self.history())
        self.channel.guild.get_member = Mock(side_effect=lambda user_id: SimpleNamespace(id=user_id, bot=user_id == 3))

    async def history(self):
        if self.first_message is not None:
            yield self.first_message

    message = TranslationMessageTests.message

    async def send(self, content, **author):
        message = self.message(content, **author)
        await self.ns['translate_discord_ticket_message'](message)
        return message

    def test_tickets_are_private_channels_with_ticket_in_a_name_or_a_listed_category(self):
        ticket = self.ns['is_discord_ticket']
        self.assertTrue(ticket(text_channel('ticket-0001', 'Support')))
        self.assertTrue(ticket(text_channel('kevin', '📩 Tickets')))
        self.assertTrue(ticket(text_channel('kevin', 'Support', category_id=77)))
        self.assertFalse(ticket(text_channel('open-a-ticket', 'Tickets', public=True)))
        self.assertFalse(ticket(text_channel('staff-chat', 'Staff')))
        self.assertFalse(ticket(SimpleNamespace(name='ticket-dm', guild=None)))

    def test_ticket_threads_count_when_private_or_under_a_private_channel(self):
        def thread(name, parent, private):
            channel = Mock(spec=discord.Thread)
            channel.name, channel.parent, channel.guild = name, parent, parent.guild
            channel.is_private.return_value = private
            return channel
        ticket = self.ns['is_discord_ticket']
        self.assertTrue(ticket(thread('kevin', text_channel('tickets', 'Support', public=True), private=True)))
        self.assertTrue(ticket(thread('ticket-7', text_channel('helpdesk', 'Support'), private=False)))
        self.assertFalse(ticket(thread('ticket-7', text_channel('helpdesk', 'Support', public=True), private=False)))
        self.assertFalse(ticket(thread('kevin', text_channel('general', 'Community', public=True), private=True)))

    def test_staff_are_the_members_with_the_staff_role(self):
        source = (ROOT / 'discord_bot/utils/permissions.py').read_text(encoding='utf-8')
        self.assertIn('STAFF_ROLE_ID = 1024761808428466257', source)
        staff = load_code('discord_bot/utils/permissions.py', dict(STAFF_ROLE_ID=self.STAFF), {'is_staff_member'})
        role = lambda *ids: SimpleNamespace(roles=[SimpleNamespace(id=item) for item in ids])
        self.assertTrue(staff['is_staff_member'](role(5, self.STAFF)))
        self.assertFalse(staff['is_staff_member'](role(5, 1364999900135165993)))
        self.assertFalse(staff['is_staff_member'](SimpleNamespace()))  # someone who has left the server

    async def test_the_opener_s_message_is_read_and_a_staff_reply_translated(self):
        message = await self.send('hola, mi servidor no funciona')
        self.ns['show_english'].assert_awaited_once_with(self.channel, '500', 'hola, mi servidor no funciona', reply_to=message)
        reply = await self.send('what is the issue', roles=[self.STAFF], author_id=20)
        self.ns['translate_for_opener'].assert_awaited_once_with('500', 'what is the issue')
        self.ns['show_translation'].assert_awaited_once_with(self.channel, TranslationMessageTests.REPLY, 'es', reply_to=reply)
        # Nothing is posted for a reply with nothing to translate, or while the ticket is in English.
        self.ns['translate_for_opener'].return_value = None
        await self.send('?', roles=[self.STAFF], author_id=20)
        self.ns['show_translation'].assert_awaited_once()

    async def test_a_staff_member_who_opened_the_ticket_is_treated_as_its_opener(self):
        await self.send('hola, mi servidor no funciona', roles=[self.STAFF], author_id=9)
        self.ns['show_english'].assert_awaited_once()
        self.ns['translate_for_opener'].assert_not_awaited()
        # The opening message is looked up once per channel, not on every message.
        await self.send('gracias', roles=[self.STAFF], author_id=9)
        self.assertEqual(self.channel.history.call_count, 1)

    async def test_staff_are_staff_when_the_opener_cannot_be_identified(self):
        for first in (None, SimpleNamespace(author=SimpleNamespace(bot=False), content='<@20> hi', embeds=[]),
                      SimpleNamespace(author=SimpleNamespace(bot=True), content='<@3> <@&5> Welcome', embeds=[])):
            self.ns['_openers'].clear()
            self.ns['translate_for_opener'].reset_mock()
            self.first_message = first
            await self.send('what is the issue', roles=[self.STAFF], author_id=20)
            self.ns['translate_for_opener'].assert_awaited_once()

    async def test_staff_are_staff_when_the_bot_may_not_read_the_channel_history(self):
        refused = discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'), 'Missing Access')
        self.channel.history = Mock(side_effect=refused)
        for _ in range(2):
            await self.send('what is the issue', roles=[self.STAFF], author_id=9)
        self.assertEqual(self.ns['translate_for_opener'].await_count, 2)
        self.assertEqual(self.channel.history.call_count, 1)

    def test_a_mistyped_category_setting_is_ignored(self):
        ids = self.ns['_category_ids']
        self.assertEqual(ids([77, '78']), {77, 78})
        self.assertEqual(ids(77), {77})
        self.assertEqual(ids('77'), {77})
        for value in (None, [], 'Tickets', ['Tickets'], 7.5, [None]):
            self.assertEqual(ids(value), set(), value)

    async def test_the_opener_can_be_named_in_the_ticket_bot_s_embed(self):
        self.first_message = SimpleNamespace(author=SimpleNamespace(bot=True), content='',
                                             embeds=[SimpleNamespace(description='Ticket opened by <@!20>')])
        await self.send('hola', roles=[self.STAFF], author_id=20)
        self.ns['show_english'].assert_awaited_once()

    async def test_messages_outside_discord_tickets_are_ignored(self):
        self.ns['get_ticket_id'].return_value = 'ticket-1'  # a web ticket: the bridge translates it
        await self.send('hola, mi servidor no funciona')
        self.ns['get_ticket_id'].return_value = None
        await self.send('hola', bot=True)
        await self.send('hola', guild_id=2)
        await self.send('   ')
        self.channel.permissions_for.return_value = SimpleNamespace(view_channel=True)
        await self.send('hola, mi servidor no funciona')
        self.ns['show_english'].assert_not_awaited()
        self.ns['translate_for_opener'].assert_not_awaited()


class WebTicketTests(unittest.IsolatedAsyncioTestCase):
    """The web ticket shows the owner staff replies in their own language only."""

    REPLY = Reply('cual es el problema', 'Traducción automática')

    def setUp(self):
        self.queries = []
        self.stored_reply = 41
        self.events = []
        self.show_english = AsyncMock()
        self.translate_for_opener = AsyncMock(return_value=None)
        self.show_translation = AsyncMock(side_effect=lambda *args, **options: self.events.append('translation shown'))
        self.emails = []
        self.ns = load_code('discord_bot/ticket_sync.py', dict(
            discord=discord, logger=Mock(), datetime=__import__('datetime'), DISCORD_GUILD_ID=1,
            TICKET_DISCORD_CATEGORY_ID=2, DatabaseManager=SimpleNamespace(execute_query=self.query),
            show_english=self.show_english, translate_for_opener=self.translate_for_opener,
            show_translation=self.show_translation, web_reply=lambda reply: f'[{reply.label}] {reply.text}',
            get_ticket_id=lambda channel_id: 'ticket-1', _lookup_staff_user=lambda discord_id: (12, 'staff@example.com'),
            _get_ticket_owner_email=lambda ticket_id: 'Owner@example.com',
            _apply_ticket_channel_status=AsyncMock(side_effect=lambda *args: self.events.append('channel renamed')),
            _get_smtp_config=lambda: {'MAIL_SERVER': 'smtp.example.com'},
            send_email_without_app_context=lambda *args: self.emails.append(args),
        ), {'_translate_web_message', 'process_discord_message', '_discord_ids_configured'})
        self.channel = Mock(spec=discord.TextChannel)
        self.channel.id, self.channel.category_id = 500, 2

    def query(self, sql, values=()):
        self.queries.append((' '.join(sql.split()), values))
        self.events.append(sql.split()[0] + ' ' + ('tickets' if 'tickets SET' in sql else 'comment'))
        return (self.stored_reply,) if sql.startswith('SELECT MAX') else None

    def written(self, statement):
        return [values for sql, values in self.queries if sql.startswith(statement)]

    def message(self, content, attachments=()):
        return SimpleNamespace(id=900, content=content, channel=self.channel, guild=SimpleNamespace(id=1),
                               author=SimpleNamespace(id=9002, bot=False, display_name='Sam'),
                               attachments=[SimpleNamespace(url=url) for url in attachments], add_reaction=AsyncMock())

    async def test_what_the_owner_wrote_is_shown_in_english_in_discord_only(self):
        await self.ns['_translate_web_message']('channel', 'ticket-1', 'owner@example.com', '我的服务器无法启动', '帮助')
        self.show_english.assert_awaited_once_with('channel', 'ticket-1', '帮助\n我的服务器无法启动')
        await self.ns['_translate_web_message']('channel', 'ticket-1', 'owner@example.com', '谢谢')
        self.assertEqual(self.show_english.await_args.args, ('channel', 'ticket-1', '谢谢'))
        # Nothing on the website changes for the owner's own messages.
        self.assertEqual(self.queries, [])
        self.translate_for_opener.assert_not_awaited()

    async def test_a_staff_reply_from_discord_reaches_the_web_ticket_in_the_owner_s_language_only(self):
        self.translate_for_opener.return_value = (self.REPLY, 'es')
        message = self.message('what is the issue')
        await self.ns['process_discord_message'](Mock(), message)
        self.translate_for_opener.assert_awaited_once_with('ticket-1', 'what is the issue')
        (ticket_id, user_id, comment, _), = self.written('INSERT INTO ticket_comments')
        self.assertEqual((ticket_id, user_id, comment), ('ticket-1', 12, '[Traducción automática] cual es el problema'))
        # The English stays in Discord: it is in neither the ticket nor the email about it.
        self.assertNotIn('what is the issue', str(self.queries) + str(self.emails))
        self.assertIn('[Traducción automática] cual es el problema', self.emails[0][2])
        self.show_translation.assert_awaited_once_with(self.channel, self.REPLY, 'es', reply_to=message, web=True)
        message.add_reaction.assert_awaited_once()

    async def test_staff_see_the_translation_without_waiting_for_the_channel_rename(self):
        self.translate_for_opener.return_value = (self.REPLY, 'es')
        await self.ns['process_discord_message'](Mock(), self.message('what is the issue'))
        self.assertEqual(self.events, ['INSERT comment', 'UPDATE tickets', 'translation shown', 'channel renamed'])

    async def test_attachments_are_kept_with_a_translated_reply(self):
        self.translate_for_opener.return_value = (self.REPLY, 'es')
        await self.ns['process_discord_message'](Mock(), self.message('what is the issue', ['https://cdn.example.com/a.png']))
        self.assertEqual(self.written('INSERT INTO ticket_comments')[0][2],
                         '[Traducción automática] cual es el problema\n\nAttachment: https://cdn.example.com/a.png')
        self.queries.clear()
        await self.ns['process_discord_message'](Mock(), self.message('', ['https://cdn.example.com/a.png']))
        self.assertEqual(self.written('INSERT INTO ticket_comments')[0][2], 'Attachment: https://cdn.example.com/a.png')
        self.assertEqual(self.translate_for_opener.await_count, 1)

    async def test_a_reply_is_stored_as_written_when_there_is_no_translation(self):
        # An English ticket, a reply such as "?", or the translation service not answering.
        message = self.message('what is the issue')
        await self.ns['process_discord_message'](Mock(), message)
        self.assertEqual(self.written('INSERT INTO ticket_comments')[0][2], 'what is the issue')
        self.show_translation.assert_not_awaited()
        message.add_reaction.assert_awaited_once()

    async def test_a_staff_reply_from_the_website_is_replaced_by_its_translation(self):
        self.translate_for_opener.return_value = (self.REPLY, 'es')
        await self.ns['_translate_web_message'](self.channel, 'ticket-1', 'staff@example.com', 'what is the issue', 'Title')
        self.translate_for_opener.assert_awaited_once_with('ticket-1', 'what is the issue')
        # The website stored the English reply: that reply is found and rewritten, nothing is added.
        self.assertEqual(self.written('SELECT MAX(ticket_comments.id)'), [('ticket-1', 'staff@example.com', 'what is the issue')])
        self.assertEqual(self.written('UPDATE ticket_comments'), [('[Traducción automática] cual es el problema', 41)])
        self.assertEqual(self.written('INSERT'), [])
        self.show_translation.assert_awaited_once_with(self.channel, self.REPLY, 'es', web=True)
        self.show_english.assert_not_awaited()

    async def test_nothing_on_the_website_changes_without_a_translation_or_a_stored_reply(self):
        await self.ns['_translate_web_message'](self.channel, 'ticket-1', 'staff@example.com', 'what is the issue')
        self.assertEqual(self.queries, [])
        self.translate_for_opener.return_value = (self.REPLY, 'es')
        self.stored_reply = None
        await self.ns['_translate_web_message'](self.channel, 'ticket-1', 'staff@example.com', 'what is the issue')
        self.assertEqual(self.written('UPDATE'), [])
        self.show_translation.assert_not_awaited()

    async def test_a_translation_failure_does_not_break_the_ticket_bridge(self):
        self.show_english.side_effect = RuntimeError('boom')
        self.translate_for_opener.side_effect = RuntimeError('boom')
        await self.ns['_translate_web_message'](self.channel, 'ticket-1', 'owner@example.com', 'hola')
        await self.ns['_translate_web_message'](self.channel, 'ticket-1', 'staff@example.com', 'hi')
        self.assertEqual(self.queries, [])

    def test_the_bridge_and_the_bot_hand_messages_to_translation(self):
        sync = (ROOT / 'discord_bot/ticket_sync.py').read_text(encoding='utf-8')
        self.assertEqual(sync.count('await _translate_web_message(channel, ticket_id, opener_email, initial_message, title)'), 2)
        self.assertIn('await _translate_web_message(channel, ticket_id, author_email, message)', sync)
        # Discord can hold a channel rename back for minutes: a translation never waits behind one.
        self.assertEqual(len(re.findall(r'await _translate_web_message\(.*\)\n\s+await _apply_ticket_channel_status\(', sync)), 3)
        bot = (ROOT / 'discord_bot/bot.py').read_text(encoding='utf-8')
        self.assertIn('await translate_discord_ticket_message(message)', bot)


class TicketLanguageStoreTests(unittest.TestCase):
    def setUp(self):
        self.rows = {}
        self.ns = load_code('managers/ticket_discord_manager.py', dict(
            Optional=__import__('typing').Optional, DatabaseManager=SimpleNamespace(execute_query=self.query),
        ), {'get_ticket_language', 'set_ticket_language'})

    def query(self, sql, values):
        if sql.lstrip().startswith('SELECT'):
            return (self.rows[values[0]],) if values[0] in self.rows else None
        self.assertIn('ON DUPLICATE KEY UPDATE language', sql)
        self.rows[values[0]] = values[1]

    def test_a_language_is_kept_per_web_ticket_or_discord_channel(self):
        self.assertIsNone(self.ns['get_ticket_language']('ticket-1'))
        self.ns['set_ticket_language']('ticket-1', 'es')
        self.ns['set_ticket_language'](500, 'zh-CN')
        self.ns['set_ticket_language']('ticket-1', 'pt')
        self.assertEqual(self.ns['get_ticket_language']('ticket-1'), 'pt')
        self.assertEqual(self.ns['get_ticket_language'](500), 'zh-CN')
        self.assertEqual(self.rows, {'ticket-1': 'pt', '500': 'zh-CN'})


if __name__ == '__main__':
    unittest.main()
