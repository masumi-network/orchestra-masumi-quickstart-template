from logging import Logger
from mainframe_orchestra import Agent, Task, Conduct, WebTools, OpenaiModels, set_verbosity

# To view detailed prompt logs, set verbosity to 1. For less verbose logs, set verbosity to 0.
set_verbosity(1)

class ResearchCrew:
    def __init__(self, logger: Logger):
        self.logger = logger
        
        # Create agents
        self.researcher = Agent(
            agent_id="researcher",
            role="Researcher",
            goal="Search for comprehensive information and data about the given topic",
            attributes="You are thorough, detail-oriented, and fact-based. You verify information from multiple sources.",
            llm=OpenaiModels.gpt_4o,
            tools=[WebTools.serper_search]
        )
        
        self.writer = Agent(
            agent_id="writer",
            role="Writer",
            goal="Create well-structured, engaging content based on research",
            attributes="You are creative, articulate, and skilled at explaining complex topics in an accessible way.",
            llm=OpenaiModels.gpt_4o
        )
        
        self.editor = Agent(
            agent_id="editor",
            role="Editor",
            goal="Ensure content is polished, accurate, and properly formatted",
            attributes="You have excellent attention to detail, grammar skills, and knowledge of markdown formatting.",
            llm=OpenaiModels.gpt_4o
        )
        
        self.conductor = Agent(
            agent_id="conductor",
            role="Research Conductor",
            goal="Coordinate research and writing tasks to produce high-quality content",
            attributes="You coordinate between research, writing, and editing to produce high-quality markdown articles. You ensure the final output is comprehensive and well-structured.",
            llm=OpenaiModels.gpt_4o,
            tools=[Conduct.conduct_tool(self.researcher, self.writer, self.editor)]
        )

    def create_task(self, topic):
        """Create a research task with the given topic"""
        task = Task.create(
            agent=self.conductor,
            context=f"User requested research on: {topic}",
            instruction=f"Generate a comprehensive, well-researched article in Markdown format about: {topic}"
        )
        return task