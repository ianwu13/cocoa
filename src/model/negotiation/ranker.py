from itertools import izip
import random
from src.lib.bleu import compute_bleu
import numpy as np

def add_ranker_arguments(parser):
    parser.add_argument('--ranker', choices=['random', 'cheat', 'encdec'], help='Ranking model')

class BaseRanker(object):
    def __init__(self):
        self.name = 'ranker'
        self.perplexity = False

    def select(self, batch):
        raise NotImplementedError

class RandomRanker(BaseRanker):
    def __init__(self):
        super(RandomRanker, self).__init__()
        self.name = 'ranker-random'

    @classmethod
    def select(cls, batch):
        responses = [c[0] if len(c) > 0 else [] for c in batch['token_candidates']]
        return responses

class CheatRanker(BaseRanker):
    def __init__(self):
        super(CheatRanker, self).__init__()
        self.name = 'ranker-cheat'

    @classmethod
    def select(cls, batch):
        candidates = batch['token_candidates']
        targets = batch['decoder_tokens']
        responses = []
        for c, target in izip(candidates, targets):
            if not len(target) > 0:
                response = []
            else:
                scores = [compute_bleu(r, target) for r in c]
                if len(scores) == 0:
                    response = []
                else:
                    response = c[np.argmax(scores)]
            responses.append(response)
        return responses

class EncDecRanker(BaseRanker):
    def __init__(self, model):
        super(EncDecRanker, self).__init__()
        self.model = model
        self.name = 'ranker-encdec'

    def set_tf_session(self, sess):
        self.sess = sess

    def _get_feed_dict_args(self, batch, encoder_init_state=None):
        encoder_args = {'inputs': batch['encoder_inputs'],
                'init_state': encoder_init_state,
                }
        decoder_args = {'inputs': batch['decoder_inputs'],
                'targets': batch['targets'],
                'context': batch['context'],
                }
        kwargs = {'encoder': encoder_args,
                'decoder': decoder_args,
                }
        return kwargs

    def select(self, batch, encoder_init_state):
        token_candidates = batch['token_candidates']
        candidates = batch['candidates']
        batch_size, num_candidate, _ = candidates.shape
        kwargs = self._get_feed_dict_args(batch, encoder_init_state)
        candidates_loss = np.zeros([batch_size, num_candidate])  # (batch_size, num_candidates)
        for i in xrange(num_candidate):
            candidate = candidates[:, i, :]  # (batch_size, seq_len)
            kwargs['decoder']['inputs'] = candidate[:, :-1]
            kwargs['decoder']['targets'] = candidate[:, 1:]
            feed_dict = self.model.get_feed_dict(**kwargs)
            batch_loss = self.sess.run(self.model.seq_loss, feed_dict=feed_dict)
            candidates_loss[:, i] = batch_loss
        #best_candidates = np.argmax(-1. * candidates_loss, axis=1)

        exp_x = np.exp(-1. * candidates_loss)
        probs = exp_x / np.sum(exp_x, axis=1, keepdims=True)
        best_candidates = []
        for i in xrange(batch_size):
            try:
                best_candidates.append(np.random.choice(num_candidate, 1, p=probs[i])[0])
            except ValueError:
                best_candidates.append(np.argmax(probs[i]))


        responses = [token_candidates[i][j] if j < len(token_candidates[i]) else []
                for i, j in enumerate(best_candidates)]

        # Decoder the true utterance to get the state
        kwargs['decoder']['inputs'] = batch['decoder_inputs']
        kwargs['decoder']['targets'] = batch['targets']
        feed_dict = self.model.get_feed_dict(**kwargs)
        true_final_state = self.sess.run(self.model.final_state, feed_dict=feed_dict)

        return {
                'responses': responses,
                'true_final_state': true_final_state,
                'cheat_responses': CheatRanker.select(batch),
                'IR_responses': RandomRanker.select(batch),
                'candidates': token_candidates,
                }

