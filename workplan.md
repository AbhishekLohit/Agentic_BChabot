## Plan of action for Capstone

Date: 
1. Submission: 27 Nov
2. Dev Completion Goal: 8 Nov 
3. Testing completion: 15 Nov
3. Deck preparation: 15 Nov

`What are we solving`
1. Personalized financial information to the user in a Agentic way.
2. Issue resolution for non fraud/non-fraud disputes, card block etc

1. `Improve the existing data lake`.
	a. Redesign the source dataset it `(PRIORITY)`. See what is useful and whats not. What can be added.
2. `Enhancement to the agentssexisting code logic to make it fail safe`.
	a. Analyze the exisitning flow and the prompts health to understand what needs to be improved.
3. `Featutes Addition: Should be scoped around what can be answered using transactions preferably.`
	a. Complaints handling: Non fraud and fraud Disputes handling(for transactions), blocked card handling.
	b. Process automation: Opening a bank account, Applying for a loan:
	c. Help center to answer any bank related FAQ's( RAG Component: in the existing code but in nascent state)	
4. `Evaluation framework`
	a. Creation of a GOLDEN DATASET `(PRIORITY)`.
	b. Evaluation strategy - LLM as a judge for each agent performance monitoring.
5. `Deployement` - AWS ??
6. `Improvements to guardrails`
7. `DB to be used`
8. `UI Experience` 
9. `Confirmation on ambiguity`
10. `MCPs`
11. `Multiple type questions`
12. `Visual Help Guide for the user`: Chit-chat(Agent for user guide)
13. `Human in loop`
14. `User feedback: Sentiment`
15. `User authentication` : Important


1. Data(sql table), Golden Dataset and architecture(multi agent with orchestrator and sub agent with tools) - Abhishek
2. Prompts and initial test runs 
3. RAG design(rag docs, chunking strat., embedding and retrival) - Gopika and Aruna
4. Complaints handling plan: how this needs to be implemented, what will be the design flow - Mantej
5. Evaluation framework : 
6. UI experience



	
	