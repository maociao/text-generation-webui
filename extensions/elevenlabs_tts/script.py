import html
import re
from os import getenv
from pathlib import Path

import elevenlabs
import gradio as gr

from modules import chat, shared, ui_chat
from modules.logging_colors import logger
from modules.utils import gradio

params = {
    'activate': True,
    'api_key': getenv("ELEVENLABS_API_KEY") or None,
    'selected_voice': 'None',
    'autoplay': False,
    'show_text': True,
    'model': 'eleven_multilingual_v2',
}

voices = None
wav_idx = 0
LANG_MODELS = ['eleven_monolingual_v1', 'eleven_multilingual_v1', 'eleven_multilingual_v2']


def update_api_key(key):
    params['api_key'] = key
    if key is not None:
        elevenlabs.set_api_key(key)


def refresh_voices():
    global params
    your_voices = elevenlabs.voices()
    voice_names = [voice.name for voice in your_voices]
    return voice_names


def refresh_voices_dd():
    all_voices = refresh_voices()
    return gr.Dropdown.update(value=all_voices[0], choices=all_voices)

def get_voice_from_file(character, mode):

    if mode == 'instruct':
        return None

    voice_file = Path(f'logs/chat/{character}/.11lvoice')

    if voice_file.exists():
        with open(voice_file, "rb") as f:
            voice = f.read()
    else:
        voice = None

    return voice


def remove_tts_from_history(history):
    for i, entry in enumerate(history['internal']):
        history['visible'][i] = [history['visible'][i][0], entry[1]]

    return history


def toggle_text_in_history(history):
    for i, entry in enumerate(history['visible']):
        visible_reply = entry[1]
        if visible_reply.startswith('<audio'):
            if params['show_text']:
                reply = history['internal'][i][1]
                history['visible'][i] = [history['visible'][i][0], f"{visible_reply.split('</audio>')[0]}</audio>\n\n{reply}"]
            else:
                history['visible'][i] = [history['visible'][i][0], f"{visible_reply.split('</audio>')[0]}</audio>"]

    return history


def remove_surrounded_chars(string):
    # this expression matches to 'as few symbols as possible (0 upwards) between any asterisks' OR
    # 'as few symbols as possible (0 upwards) between an asterisk and the end of the string'
    return re.sub('\*[^\*]*?(\*|$)', '', string)


def state_modifier(state):
    if not params['activate']:
        return state

    state['stream'] = False
    return state


def input_modifier(string):
    if not params['activate']:
        return string

    shared.processing_message = "*Is recording a voice message...*"
    return string


def history_modifier(history):
    # Remove autoplay from the last reply
    if len(history['internal']) > 0:
        history['visible'][-1] = [
            history['visible'][-1][0],
            history['visible'][-1][1].replace('controls autoplay>', 'controls>')
        ]

    return history


def output_modifier(string, state):
    global params, wav_idx

    if not params['activate']:
        return string
    
    original_string = string
    string = remove_surrounded_chars(string)
    string = string.replace('"', '')
    string = string.replace('“', '')
    string = string.replace('\n', ' ')
    string = string.strip()
    if string == '':
       return original_string

    history_path = state['history']['path']
    output_dir = Path(history_path).parent / "outputs"
    if not output_dir.is_dir():
        output_dir.mkdir()
        
    voice_file = Path(history_path).parent.parent / ".11lvoice"
    if not voice_file.exists():
        with open(voice_file, "w") as f:
            f.write(params['selected_voice'])
    else:
        with open(voice_file, "r") as f:
            params.update({'selected_voice': f.read()})
                
    files = list(Path(f'{output_dir}').glob('*.mp3'))
    files.sort(key=lambda x: int(x.stem))

    if len(files) > 0:
        wav_idx = int(Path(files[-1]).stem) + 1

    output_file = Path(f'{output_dir}/{wav_idx:06d}.mp3'.format(wav_idx))
    print(f'Outputting audio to {str(output_file)}')
    try:
        audio = elevenlabs.generate(text=html.unescape(string), voice=params['selected_voice'], model=params['model'])
        elevenlabs.save(audio, str(output_file))

        autoplay = 'autoplay' if params['autoplay'] else ''
        string = f'<audio src="file/{output_file.as_posix()}" controls {autoplay}></audio>'

    except elevenlabs.api.error.UnauthenticatedRateLimitError:
        string = "🤖 ElevenLabs Unauthenticated Rate Limit Reached - Please create an API key to continue\n\n"
    except elevenlabs.api.error.RateLimitError:
        string = "🤖 ElevenLabs API Tier Limit Reached\n\n"
    except elevenlabs.api.error.APIError as err:
        string = f"🤖 ElevenLabs Error: {err}\n\n"

    if params['show_text']:
        string += f'\n\n{original_string}'

    shared.processing_message = "*Is typing...*"
    return string


def ui():
    global voices

    with gr.Row():
        if params['api_key']:
            api_key = gr.Textbox(value=params['api_key'], label='API Key')
            update_api_key(params['api_key'])
        else:
            api_key = gr.Textbox(placeholder="Enter your API key.", label='API Key')

    # Gradio elements
    with gr.Row():
        activate = gr.Checkbox(value=params['activate'], label='Activate TTS')
        autoplay = gr.Checkbox(value=params['autoplay'], label='Play TTS automatically')
        show_text = gr.Checkbox(value=params['show_text'], label='Show message text under audio player')

    with gr.Row():
        if not voices:
            voices = refresh_voices()
            selected = params['selected_voice']
            if selected == 'None':
                existing_voice = get_voice_from_file(gradio('character_menu'), gradio('mode'))
                params['selected_voice'] = existing_voice or voices[0]
            elif selected not in voices:
                logger.error(f'Selected voice {selected} not available, switching to {voices[0]}')
                params['selected_voice'] = voices[0]

        voice = gr.Dropdown(value=params['selected_voice'], choices=voices, label='TTS Voice')
        refresh = gr.Button(value='Refresh')

    with gr.Row():
        model = gr.Dropdown(value=params['model'], choices=LANG_MODELS, label='Language model')

    with gr.Row():
        convert = gr.Button('Permanently replace audios with the message texts')
        convert_cancel = gr.Button('Cancel', visible=False)
        convert_confirm = gr.Button('Confirm (cannot be undone)', variant="stop", visible=False)

    # Convert history with confirmation
    convert_arr = [convert_confirm, convert, convert_cancel]
    convert.click(lambda: [gr.update(visible=True), gr.update(visible=False), gr.update(visible=True)], None, convert_arr)
    convert_confirm.click(
        lambda: [gr.update(visible=False), gr.update(visible=True), gr.update(visible=False)], None, convert_arr).then(
        remove_tts_from_history, gradio('history'), gradio('history')).then(
        chat.save_history, gradio('history', 'unique_id', 'character_menu', 'mode'), None).then(
        chat.redraw_html, gradio(ui_chat.reload_arr), gradio('display'))

    convert_cancel.click(lambda: [gr.update(visible=False), gr.update(visible=True), gr.update(visible=False)], None, convert_arr)

    # Toggle message text in history
    show_text.change(
        lambda x: params.update({"show_text": x}), show_text, None).then(
        toggle_text_in_history, gradio('history'), gradio('history')).then(
        chat.save_history, gradio('history', 'unique_id', 'character_menu', 'mode'), None).then(
        chat.redraw_html, gradio(ui_chat.reload_arr), gradio('display'))

    # Event functions to update the parameters in the backend
    activate.change(lambda x: params.update({'activate': x}), activate, None)
    voice.change(lambda x: params.update({'selected_voice': x}), voice, None)
    api_key.change(update_api_key, api_key, None)
    model.change(lambda x: params.update({'model': x}), model, None)
    # connect.click(check_valid_api, [], connection_status)
    refresh.click(refresh_voices_dd, [], voice)
    # Event functions to update the parameters in the backend
    autoplay.change(lambda x: params.update({"autoplay": x}), autoplay, None)
