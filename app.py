import json
import uuid
import wave
import os
import threading
import random
import logging
import time
import queue
from flask import Flask, request
from twilio.twiml.voice_response import VoiceResponse, Gather
from google import genai
from google.genai import types
from edgeable import GraphDatabase
from pydantic import BaseModel

logger = logging.getLogger("read2me")


graph = GraphDatabase()
client = genai.Client(api_key=genai_key)

voice_config = types.GenerateContentConfig(
    response_modalities=["AUDIO"],
    speech_config=types.SpeechConfig(
        multi_speaker_voice_config=types.MultiSpeakerVoiceConfig(
            speaker_voice_configs=[
                types.SpeakerVoiceConfig(
                    speaker='Lee',
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                            voice_name='Algenib',
                        )
                    )
                ),
                types.SpeakerVoiceConfig(
                    speaker='Katie',
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                            voice_name='Kore',
                        )
                    )
                ),
            ]
        )
    )
)

# Create an async queue for pages that need to be loaded
q = queue.Queue(maxsize=50)
def task_runner():
    while True:
        task = q.get()
        get_next_page(task["id"], task["prompt"])
        time.sleep(0.1)

# Start multiple threads to process the queue
for index in range(5):
    thread = threading.Thread(target=task_runner)
    thread.start()

def get_next_page(pageId, prompt):

    # Check if the page already exists in the graph and has a story
    # and if the audio file for the page exists
    if graph.get_node(pageId) is not None and graph.get_node(pageId).get_property("story") is not None and os.path.exists(f"./audio/{pageId}.wav"):
        return graph.get_node(pageId)

    # Define the response schema for the Gemini model
    class ResponsePage(BaseModel):
        story: str
        prompts: list[str]

    # Generate the story page using the Gemini model
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction="You are a choose your own adventure story generator for kids. I will provide a story summary, and a portion of the story text. You create story pages that will take about one minute to read, followed by instructions for chosing the next path in the story.  Don't reveal key plot elements until after the first several pages. The fullstory is about 15 pages in length, with a clear beginning, middle, and end with a kid-friendly moral.  All responses are in the form of a raw json object convert to a string that has a 'story' attribute with the portion that is read to the user, as well as a 'prompts' attribute which is an array of strings that describe specific different actions the listener can take to continue the plot. There are between two and four possible actions for the reader to chose, only if the story has come to a conculsion and at the end should there be zero actions to chose from.'",
            response_mime_type="application/json",
            response_schema=list[ResponsePage]
        ),
    )
    respobj = json.loads(response.text[response.text.find("{"):1+response.text.rfind("}")])

    pageNode = graph.put_node(pageId, {'type': 'page'})
    pageNode.set_property("story", respobj["story"])
    prompts = respobj["prompts"]
    for prompt in prompts:
        promptNode = graph.put_node(str(uuid.uuid4()), {'type': 'page', 'story': None})
        pageNode.attach(promptNode, {'type':'action', 'action': prompt})
        promptNode.attach(pageNode, {'type':'previous'})    

    # Add the story page content
    contents = "Lee: " + respobj["story"] + "\n"

    # Add the action choices
    numbers = [
        "one",
        "two",
        "three",
        "four"
    ]
    contents += random.choice([
        "Katie: Now it is your turn to help us continue the story. You can choose what happens next: ",
        "Katie: What do you think should happen next? ",
        "Katie: I'm enjoying this story. What do you want to happen next? ",
        "Katie: Now its your turn. Pick what happens next in our story. ",
        "Katie: Let's see what happens next. You can choose what happens next in the story. ",
        "Katie: "
    ])
    for index, prompt in enumerate(respobj["prompts"]):
        contents += "Press " + numbers[index] + " for " + prompt

    # If there are no prompts, then this is the end of the story
    if(len(respobj["prompts"]) == 0):
        contents = "Lee: The end!\n"
        contents += random.choice([
            "Katie: Well, that's the end of our story. I hope you enjoyed it! If you want to hear it again, just call back and we can read it together.\n",
            "Katie: That was a great story! I hope you enjoyed it. If you want to another story, just call back and we can keep reading together!\n",
            "Katie: I had a lot of fun reading this story with you! If you want to hear it again, just call back and we can read it together. See you next time!\n"
        ])
        contents += random.choice([
            "Lee: Goodbye for now!\n",
            "Lee: Thanks for reading with us! Goodbye!\n",
            "Lee: I hope you enjoyed the story! Goodbye!\n",
            "Lee: It was fun reading with you! Goodbye!\n",
        ])

    # Generate the audio for the story page using the Gemini model
    try:
        audio_data = client.models.generate_content(
            model="gemini-2.5-flash-preview-tts",
            contents=contents,
            config=voice_config
        ).candidates[0].content.parts[0].inline_data.data
        with wave.open(f"./audio/{pageId}.wav", 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(24000)
            wf.writeframes(audio_data)
    except Exception as e:
        # TODO handle by re-enqueing?
        logger.error(f"Error generating audio for page {pageId}: {e}")
        return None
        
    graph.save()
    return pageNode

intros = [
    # Introduction variation 1
    "Lee: Hello! I'm excited to be able to share a special story with you. My name is Lee and I really enjoy reading.  I'm joined by my friend Katie who will be helping us out with today's story.\n"
     + "Katie: That's right, I'm Katie and I'm looking forward to being able to help. Lee and I are going to be reading a story together, and after each page I will be giving you some choices that will help shape the story. You can press a button on your phone to pick what you want to happen next.\n"
     + "Lee: It's going to be really fun to see how the story turns out, should we begin?\n"
     + "Katie: Let's start, Lee!\n",

    # Introduction variation 2
    "Katie: Hello! I'm excited that we have a story we are looking forward to sharing with you. My name is Katie and I really enjoy reading.  I'm joined by my friend Lee who will be helping read today's story.\n"
     + "Lee: That's right, I'm Lee and I'm looking forward to being able to read wtith you today. Katie and I are going to sharing this story together, I'll be reading and Katie will help you pick what you want to happen next as you shape how the story goes.\n"
     +" Katie: After each page I will be giving you some choices that will help shape the story. You can press a button on your phone to pick what you want to happen next.\n"
     + "Lee: It's going to be really fun to see how the story turns out, should we begin?\n"
     + "Katie: Let's start, Lee!\n",

    # Introduction variation 3
    "Lee: Hello this is Lee!\n"
     + "Katie: And this is Katie, I'm here too!\n"
     + "Lee: We are excited to be able to share a special story with you. I'm joining my friend Katie so we can team up for today's story.\n"
     + "Katie: That's right, I'm looking forward to being able to help. Lee and I are going to be reading a story together, and after each page I will be giving you some choices that will help shape the story. You can press a button on your phone to pick what you want to happen next.\n"
     + "Lee: It's going to be really fun to see how the story turns out, should we begin?\n"
     + "Katie: Let's start, Lee!\n",

    # Introduction variation 4
    "Katie: Hello, this is Katie!\n"
     + "Lee: And I'm Lee.\n"
     + "Katie: I'm excited that we have a story we are looking forward to sharing with you. Lee is joining us today to help us read today's story.\n"
     + "Lee: That's right, I love reading so this will be a lot of fun. Katie and I are going to be sharing this story with you together, I'll be reading and after each page Katie will give you some options to pick what you want to happen next.\n"
     +" Katie: You can press a button on your phone to pick one of the options I share with you. Depending on what you choose, the story will change!\n"
     + "Lee: It's going to be really fun to see how the story turns out, should we begin?\n"
     + "Katie: Let's start, Lee!\n",
]

# Make sure the intro audio file is available
for intro_index, intro in enumerate(intros):
    if not os.path.exists(f"./audio/intro-{intro_index}.wav"):

        # Use a model that supports text-to-speech
        audio_data = client.models.generate_content(
            model="gemini-2.5-pro-preview-tts",
            contents=intro,
            config=voice_config
        ).candidates[0].content.parts[0].inline_data.data
        with wave.open(f"./audio/intro-{intro_index}.wav", 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(24000)
            wf.writeframes(audio_data)

# If there isn't already a story starting node, create one
startNodes = graph.get_nodes(lambda node: node.get_property("type")=="start")
if len(startNodes)==0:
    story_summary = "This story is the original Winnie the Pooh universe and is in the style and tone of the original author. The reader is making decisions for how Winnie the Pooh will behave in the story, following typical actions and decisions that this character would typically make. The story starts with Poo discovering in the first few pages that the tree that holds the beehive has fallen and the bees need help finding a new home. Throught the rest of the story he works with Christopher Robin to help the bees move into a new home in a new tree. By the end of the story, the bees have been moved to a new tree and share their appreciation by giving Pooh and his friends some honey. The story is about friendship, helping others, and the joy of sharing. It is a lighthearted and fun story that is suitable for children of all ages."

    pageId = str(uuid.uuid4())
    pageNode = get_next_page(pageId, f"{story_summary}\n Respond with the first page of the story.")
    # TODO add the story outline to the starting node
    pageNode.set_property("type", "start")
    pageNode.set_property("summary", story_summary)
    graph.save()

    startNodes = graph.get_nodes(lambda node: node.get_property("type")=="start")

# Choose a story starting node at random
pageNode = random.choice(startNodes)
if not os.path.exists(f"./audio/{pageNode.get_id()}.wav"):
    get_next_page(pageNode.get_id(), f"{pageNode.get_property("summary")}\n Respond with the first page of the story.")

# Enqueue the next possible actions for the first page
for edge in pageNode.get_edges(lambda edge: edge.get_property('type')=='action'):
    q.put({
        "id": edge.get_destination().get_id(), 
        "prompt": pageNode.get_property("summary") + "\n Here is the first page of the story: "+pageNode.get_property("story")+"\n Respond with the next page of the story once the user chooses the action '"+edge.get_property("action")+"'"
    })

# Start the API server
app = Flask(__name__, static_folder='audio')
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 86400 # twenty four hours

# Route for initial page
@app.route("/", methods=['GET', 'POST'])
def voice():
    intro_index = random.randint(0, len(intros)-1)
    resp = VoiceResponse()
    resp.play(f"{domain}/audio/intro-{intro_index}.wav")
    resp.play(f"{domain}/audio/flip.wav")
    startNodes = graph.get_nodes(lambda node: node.get_property("type")=="start")

    logger.info(startNodes[0].get_property("story"))
    gather = Gather(num_digits=1, action=f"{domain}/next?id={startNodes[0].get_id()}", method='POST', timeout=10)
    gather.play(f"{domain}/audio/{startNodes[0].get_id()}.wav")    
    resp.append(gather)
    return str(resp)

# Route for the next page in the story
@app.route("/next", methods=['GET', 'POST'])
def next():


    # Get the current node
    node = graph.get_node(request.args.get('id'))

    # Ge the user's choice for the next story action
    # TODO: handle an invalid number selected
    selected_prompt = int(request.values['Digits'])-1
    if selected_prompt < 0 or selected_prompt >= len(node.get_edges(lambda edge: edge.get_property('type')=='action')):
        resp = VoiceResponse()
        gather = Gather(num_digits=1, action=f"{domain}/next?id={node.get_id()}", method='POST', timeout=10)
        gather.say("Invalid choice. Please try again.") # TODO Use voice model
        resp.append(gather)
        return str(resp)
    node = node.get_edges(lambda edge: edge.get_property('type')=='action')[selected_prompt].get_destination()

    # If this audio isn't available yet, then introduce a delay
    if not os.path.exists(f"./audio/{node.get_id()}.wav"):

        resp = VoiceResponse()
        resp.play(f"{domain}/audio/flip.wav")
        resp.pause(1)
        resp.redirect(f"{domain}/next?id={request.args.get('id')}&Digits={request.values['Digits']}", method='POST')
        return str(resp)

    # Generate the story so far by walking backwards through the previous nodes
    fullStory = ""
    pageCount = 0
    previousEdges = node.get_edges(lambda edge: edge.get_property('type')=='previous')
    previous = None if len(previousEdges) == 0 else previousEdges[0].get_destination()
    startNode = None
    while previous != None:
        pageCount += 1
        fullStory = str(previous.get_property("story")) + "\n" + fullStory
        previousEdges = previous.get_edges(lambda edge: edge.get_property('type')=='previous')
        previous = None if len(previousEdges) == 0 else previousEdges[0].get_destination()
        startNode = previous if previous.get_property("type") == "start" else None

    # Enqueue the next pages to be generated
    for edge in node.get_edges(lambda edge: edge.get_property('type')=='action'):
        q.put({
            "id": edge.get_destination().get_id(), 
            "prompt": startNode.get_property("summary") +"\n Here is the first " + str(pageCount)+" pages of the story so far: "+fullStory+"\nRespond with the next page of the story once the user chooses the action '"+edge.get_property("action")+"'"
        })

    # If the next page's audio file is available, play it
    logger.info(node.get_property("story"))
    resp = VoiceResponse()
    resp.play(f"{domain}/audio/flip.wav")
    gather = Gather(num_digits=1, action=f"{domain}/next?id={node.get_id()}", method='POST', timeout=10)
    gather.play(f"{domain}/audio/{node.get_id()}.wav")
    resp.append(gather)
    return str(resp)

if __name__ == "__main__":
    app.run(port=8080)
