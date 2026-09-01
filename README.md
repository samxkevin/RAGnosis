# RAGnosis — Neo4j + Cohere

An advanced, AI-powered medical chatbot that leverages a biomedical **Neo4j knowledge graph** and the latest **Cohere language model** to conduct multi-turn patient conversations, suggest diagnoses, and provide concise medical advice.

---

## Features

- **Persistent doctor-patient chat** – intuitive and natural multi-turn dialog
- **Intelligent follow-up questions** – asks about symptoms like a real doctor
- **Neo4j biomedical context integration** – augments responses with structured knowledge
- **Accurate, empathetic diagnoses** – uses Cohere’s LLM for medical reasoning
- **Secure credential handling** – keeps API keys out of public code
- **Conversation summary report** – displays final advice and diagnosis at the end

---

## Tech Stack

- Python (3.8+ recommended)
- [Neo4j](https://neo4j.com/) (GraphDB, queried via official Python driver)
- [Cohere AI](https://cohere.com/) (Language model chat API)
- TQDM (progress feedback)
- Simple command line interface

---

## Setup & Installation

1. **Clone this repository:**

git clone https://github.com/sathvik2903/RAGnosis.git
cd doctor-chatbot-neo4j

text

2. **Install Python dependencies:**

   pip install -r requirements.txt
   

3. **Configure credentials:**
- Copy `config.example.py` to `config.py`
- Add your own Neo4j URI, username, password, and Cohere API key in `config.py`
- *Note: `config.py` is ignored by Git for security.*

4. **Run the chatbot:**


---

## Security

- **Never share your credentials!**  
Credentials go in `config.py`, which is excluded from version control (`.gitignore`).
- If you accidentally push sensitive info, immediately **rotate your keys** and notify all contributors.

---

## Example Usage

 DoctorBot: Hello! Please tell me how you're feeling today.

 You: I've had a sore throat and headache for two days.

 DoctorBot: Thank you for sharing. Do you also have any fever or cough? How would you rate your pain?
...

Type `quit`, `exit`, or `bye` to finish and receive a summary report.

---

##  Project Structure

doctor-chatbot-neo4j/
├── doctor_chatbot.py # Main chatbot code
├── config.example.py # Template for credentials
├── .gitignore # Files ignored by Git (config.py, envs)
├── requirements.txt # Pip dependencies
└── README.md # Project documentation

## Contact: anumulasamarth008@gmail.com

For questions or support, open an issue or email [sathvik2903@gmail.com](mailto:sathvik2903@gmail.com).
