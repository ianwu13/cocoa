from session import Session

class CmdSession(Session):
    def __init__(self, agent, kb):
        super(CmdSession, self).__init__(agent)
        self.kb = kb
        print("End game using <select> x y z p; which correspondeds to book, hat, ball and your points")

    def send(self):
        message = raw_input()
        event = self.parse_input(message)
        return event

    def parse_input(self, message):
        """Parse user input from the command line.
        Args:  message (str)
        Returns: Event
        """
        raw_tokens = message.split()
        tokens = self.remove_nonprintable(raw_tokens)

        print tokens

        if len(tokens) >= 2 and tokens[0] == '<select>':
            t = [int(token) for idx, token in enumerate(tokens) if idx > 0]
            proposal = {'book': t[0], 'hat': t[1], 'ball': t[2]}
            outcome = {'item_split': proposal, 'deal_points': t[3]}
            return self.select(outcome)
        else:
            return self.message(" ".join(tokens))

    def receive(self, event):
        print event.data