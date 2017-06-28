import tensorflow as tf
import numpy as np
from src.model.util import transpose_first_two_dims, batch_embedding_lookup, EPS
from src.model.encdec import BasicEncoder, BasicDecoder, Sampler, optional_add
from src.model.sequence_embedder import AttentionRNNEmbedder
from preprocess import markers, START_PRICE
from price_buffer import PriceBuffer

class LM(object):
    def __init__(self, decoder, pad):
        self.decoder = decoder
        self.pad = pad
        self.decoder.build_model({}, set())
        self.loss, self.seq_loss, self.total_loss = self.decoder.compute_loss()
        self.perplexity = True
        self.name = 'lm'

    def get_feed_dict(self, **kwargs):
        feed_dict = kwargs.pop('feed_dict', {})
        feed_dict = self.decoder.get_feed_dict(feed_dict=feed_dict, **kwargs.pop('decoder'))
        return feed_dict

    def generate(self, sess, inputs, max_len, textint_map):
        decoder_args = {'inputs': inputs, 'textint_map': textint_map}
        decoder_output_dict = self.decoder.run_decode(sess, max_len, 1, **decoder_args)
        return {'preds': decoder_output_dict['preds']}

# TODO: refactor this class
class BasicEncoderDecoder(object):
    '''
    Basic seq2seq model.
    '''
    def __init__(self, encoder, decoder, pad, re_encode=False):
        self.pad = pad  # Id of PAD in the vocab
        self.encoder = encoder
        self.decoder = decoder
        #self.re_encode = re_encode
        self.tf_variables = set()
        self.perplexity = True
        self.name = 'encdec'
        self.build_model(encoder, decoder)

    def compute_loss(self, output_dict):
        return self.decoder.compute_loss()

    def _encoder_input_dict(self):
        return {
                'init_cell_state': None,
               }

    def _decoder_input_dict(self, encoder_output_dict):
        # TODO: clearner way to add price_history - put this in the state,
        # maybe just return encoder_output_dict...
        return {
                'init_cell_state': encoder_output_dict['final_state'],
                'encoder_embeddings': encoder_output_dict['outputs'],
                'price_history': encoder_output_dict.get('price_history', None),
               }

    def build_model(self, encoder, decoder):
        with tf.variable_scope(type(self).__name__):
            # Encoding
            encoder_input_dict = self._encoder_input_dict()
            encoder.build_model(encoder_input_dict, self.tf_variables)

            # Decoding
            decoder_input_dict = self._decoder_input_dict(encoder.output_dict)
            decoder.build_model(decoder_input_dict, self.tf_variables)

            # Re-encode decoded sequence
            # TODO: re-encode is not implemeted in neural_sessions yet
            # TODO: hierarchical
            #if self.re_encode:
            #    input_args = decoder.get_rnn_inputs_args()
            #    input_args['init_state'] = encoder.output_dict['final_state']
            #    reencode_output_dict = encoder.encode(time_major=False, **input_args)
            #    self.final_state = reencode_output_dict['final_state']
            #else:
            self.final_state = decoder.get_encoder_state(decoder.output_dict['final_state'])

            #self.targets = tf.placeholder(tf.int32, shape=[None, None], name='targets')

            # Loss
            self.loss, self.seq_loss, self.total_loss = self.compute_loss(decoder.output_dict)

    def get_feed_dict(self, **kwargs):
        feed_dict = kwargs.pop('feed_dict', {})
        feed_dict = self.encoder.get_feed_dict(feed_dict=feed_dict, **kwargs.pop('encoder'))
        feed_dict = self.decoder.get_feed_dict(feed_dict=feed_dict, **kwargs.pop('decoder'))
        return feed_dict

    def generate(self, sess, batch, encoder_init_state, max_len, textint_map=None):
        encoder_inputs = batch['encoder_inputs']
        decoder_inputs = batch['decoder_inputs']
        batch_size = encoder_inputs.shape[0]

        # Encode true prefix
        # TODO: get_encoder_args
        encoder_args = {'inputs': encoder_inputs,
                'init_cell_state': encoder_init_state,
                }
        encoder_output_dict = self.encoder.run_encode(sess, **encoder_args)

        # Decode max_len steps
        # TODO: get_decoder_args for refactor
        price_args = {'inputs': batch['decoder_price_inputs'][:, [0]]}
        decoder_args = {'inputs': decoder_inputs[:, [0]],
                'init_cell_state': encoder_output_dict['final_state'],
                'price_symbol': textint_map.vocab.to_ind('<price>'),
                'price_predictor': price_args,
                'textint_map': textint_map,
                'context': batch['context'],
                'encoder_outputs': encoder_output_dict['outputs'],
                }
        decoder_output_dict = self.decoder.run_decode(sess, max_len, batch_size, **decoder_args)

        # Decode true utterances (so that we always condition on true prefix)
        decoder_args['inputs'] = decoder_inputs
        feed_dict = self.decoder.get_feed_dict(**decoder_args)
        true_final_state = sess.run(self.final_state, feed_dict=feed_dict)
        return {'preds': decoder_output_dict['preds'],
                'prices': decoder_output_dict.get('prices', None),
                'final_state': decoder_output_dict['final_state'],
                'true_final_state': true_final_state,
                }
        #return {'preds': decoder_output_dict['preds'],
        #        'prices': None,
        #        'final_state': decoder_output_dict['final_state'],
        #        'true_final_state': true_final_state,
        #        }

class AttentionDecoder(BasicDecoder):
    '''
    Attend to encoder embeddings and/or context.
    '''
    def __init__(self, word_embedder, seq_embedder, pad, keep_prob, num_symbols, sampler=Sampler(0), sampled_loss=False, context_embedder=None, memory=('encoder',)):
        assert isinstance(seq_embedder, AttentionRNNEmbedder)
        super(AttentionDecoder, self).__init__(word_embedder, seq_embedder, pad, keep_prob, num_symbols, sampler, sampled_loss)
        self.context_embedder = context_embedder
        self.context_embedding = self.context_embedder.embed(context=('title', 'description'), step=True)  # (batch_size, context_len, embed_size)

    def get_encoder_state(self, state):
        return state.cell_state

    def _build_rnn_inputs(self, input_dict):
        inputs, mask, kwargs = super(AttentionDecoder, self)._build_rnn_inputs(input_dict)
        encoder_outputs = input_dict['encoder_embeddings']
        self.feedable_vars['encoder_outputs'] = encoder_outputs
        attention_memory = transpose_first_two_dims(encoder_outputs)  # (batch_size, seq_len, embed_size)
        attention_memory = self.context_embedding
        # TODO: attention mask
        kwargs['attention_memory'] = attention_memory
        return inputs, mask, kwargs

    def get_feed_dict(self, **kwargs):
        feed_dict = super(AttentionDecoder, self).get_feed_dict(**kwargs)
        optional_add(feed_dict, self.feedable_vars['encoder_outputs'], kwargs.pop('encoder_outputs', None))
        feed_dict = self.context_embedder.get_feed_dict(feed_dict=feed_dict, **kwargs.pop('context'))
        return feed_dict

class ContextDecoder(BasicDecoder):
    '''
    Add a context vector (category, title, description) to each decoding step.
    '''
    def __init__(self, word_embedder, seq_embedder, context_embedder, context, pad, keep_prob, num_symbols, sampler=Sampler(0), sampled_loss=False):
        super(ContextDecoder, self).__init__(word_embedder, seq_embedder, pad, keep_prob, num_symbols, sampler, sampled_loss)
        self.context_embedder = context_embedder
        #self.context = context
        self.context_embedding = self.context_embedder.embed(context)

    def _build_output(self, output_dict):
        outputs = output_dict['outputs']
        embed_size = outputs.get_shape().as_list()[-1]
        outputs = self.seq_embedder.concat_vector_to_seq(self.context_embedding, outputs)
        outputs = tf.layers.dense(outputs, embed_size, activation=tf.nn.tanh)
        # Linear layer
        outputs = transpose_first_two_dims(outputs)  # (batch_size, seq_len, output_size)
        logits = self._build_logits(outputs)
        return logits

    def _build_rnn_inputs(self, input_dict):
        inputs, mask, kwargs = super(ContextDecoder, self)._build_rnn_inputs(input_dict)  # (seq_len, batch_size, input_size)
        inputs = self.seq_embedder.concat_vector_to_seq(self.context_embedding, inputs)
        # TODO: hack
        #encoder_outputs = input_dict['encoder_embeddings']
        #self.feedable_vars['encoder_outputs'] = encoder_outputs
        return inputs, mask, kwargs

    def get_feed_dict(self, **kwargs):
        feed_dict = super(ContextDecoder, self).get_feed_dict(**kwargs)
        feed_dict = self.context_embedder.get_feed_dict(feed_dict=feed_dict, **kwargs.pop('context'))
        return feed_dict

class PriceEncoder(object):
    '''
    A wrapper of a encoder that uses a price predictor to update prices.
    '''
    def __init__(self, encoder, price_predictor):
        self.encoder = encoder
        self.price_predictor = price_predictor

    def build_model(self, word_embedder, input_dict, tf_variables, pad=0, scope=None):
        with tf.variable_scope(type(self).__name__):
            self.encoder.build_model(word_embedder, input_dict, tf_variables, pad=pad, scope=scope)
            self.price_inputs = tf.placeholder(tf.float32, shape=[None, None], name='price_inputs')  # (batch_size, seq_len)
            # Update price. partner = True. Take the price at the last time step.
            new_price_history = self.price_predictor.update_price(True, self.price_inputs)[-1]

            # Outputs
            self.output_dict = dict(self.encoder.output_dict)
            self.output_dict['price_history'] = new_prices

    def get_feed_dict(self, **kwargs):
        feed_dict = self.encoder.get_feed_dict(**kwargs)
        feed_dict[self.price_inputs] = kwargs.pop('price_inputs')
        feed_dict = self.price_predictor.get_feed_dict(feed_dict=feed_dict, **kwargs.pop('price_predictor'))
        return feed_dict

class PriceDecoder(object):
    '''
    A wrapper of a decoder that outputs <price> and a price predictor that fills in the actual price.
    '''
    def __init__(self, decoder, price_predictor):
        self.decoder = decoder
        self.price_predictor = price_predictor

    def build_model(self, word_embedder, input_dict, tf_variables, pad=0, scope=None):
        with tf.variable_scope(type(self).__name__):
            self.decoder.build_model(word_embedder, input_dict, tf_variables, pad=pad, scope=scope)
            # NOTE: output from rnn is time major
            context = transpose_first_two_dims(self.decoder.output_dict['outputs'])
            self.price_inputs = tf.placeholder(tf.float32, shape=[None, None], name='price_inputs')  # (batch_size, seq_len)
            self.price_targets = tf.placeholder(tf.float32, shape=[None, None], name='price_targets')  # (batch_size, seq_len)
            # Update price. partner = False
            new_price_history_seq = self.price_predictor.update_price(False, self.price_inputs, init_price=input_dict['price_history'])
            predicted_prices = self.price_predictor.predict_price(new_price_history_seq, context)

            # Outputs
            self.output_dict = dict(self.decoder.output_dict)
            self.output_dict['price_history'] = new_price_history_seq[-1]
            self.output_dict['price_preds'] = predicted_prices

    def compute_loss(self, pad):
        loss, seq_loss, total_loss = self.decoder.compute_loss(pad)
        price_loss = self.price_predictor.compute_loss(self.price_targets, self.output_dict['price_preds'], pad)
        loss += price_loss
        # NOTE: seq_loss and total_loss do not depend on price_loss. We're using loss for bp.
        return loss, seq_loss, total_loss

    def get_feed_dict(self, **kwargs):
        feed_dict = self.decoder.get_feed_dict(**kwargs)
        feed_dict[self.price_inputs] = kwargs.pop('price_inputs')
        optional_add(feed_dict, self.price_targets, kwargs.pop('price_targets', None))
        feed_dict = self.price_predictor.get_feed_dict(feed_dict=feed_dict, **kwargs.pop('price_predictor'))
        return feed_dict

    def get_encoder_state(self, state):
        '''
        Given the hidden state to the encoder to continue from there.
        '''
        return self.decoder.get_encoder_state(state)

    def pred_to_input(self, preds, **kwargs):
        '''
        Convert predictions to input of the next decoding step.
        '''
        textint_map = kwargs.pop('textint_map')
        inputs = textint_map.pred_to_input(preds)
        return inputs

    # TODO: no more price buffer
    def run_decode(self, sess, max_len, batch_size=1, stop_symbol=None, **kwargs):
        #return self.decoder.run_decode(sess, max_len, batch_size=batch_size, stop_symbol=stop_symbol, **kwargs)
        if stop_symbol is not None:
            assert batch_size == 1, 'Early stop only works for single instance'
        price_symbol = kwargs.pop('price_symbol')
        feed_dict = self.get_feed_dict(**kwargs)
        preds = np.zeros([batch_size, max_len], dtype=np.int32)
        prices = np.zeros([batch_size, max_len], dtype=np.float32)
        # reshape: squeeze step dim; we are only considering one step
        price_buffer = PriceBuffer(init_price_batch=kwargs['price_predictor']['inputs'].reshape(batch_size, -1))

        for i in xrange(max_len):
            logits, final_state, price = sess.run((self.output_dict['logits'], self.output_dict['final_state'], self.output_dict['prices']), feed_dict=feed_dict)
            step_preds = self.decoder.sampler.sample(logits)  # (batch_size, 1)

            preds[:, [i]] = step_preds
            if step_preds[0][0] == stop_symbol:
                break

            # Update price
            mask = (step_preds == price_symbol).reshape(-1)
            # At least one <price>
            if np.sum(mask) > 0:
                # NOTE: price is (batch_size, 1)
                price_buffer.add(price.reshape(batch_size), mask, True)
            prices[:, [i]] = price

            price_batch = price_buffer.to_price_batch()
            #print price_batch
            feed_dict = self.get_feed_dict(inputs=self.pred_to_input(step_preds, **kwargs),
                    price_predictor={'inputs': price_batch},
                    init_state=final_state)

        # TODO: hack
        #print prices
        prices = np.around(prices, decimals=2)
        return {'preds': preds, 'prices': prices, 'final_state': final_state}
