import tensorflow as tf
import os, sys
from utils import lrelu, nameop, tbn, obn



class MAGAN(object):
    """The MAGAN model."""

    def __init__(self,
        dim_b1,
        dim_b2,
        correspondence_loss,
        activation=lrelu,
        learning_rate=.001,
        restore_folder='',
        limit_gpu_fraction=1.,
        no_gpu=False,
        nfilt=64):
        """Initialize the model."""
        self.dim_b1 = dim_b1
        self.dim_b2 = dim_b2
        self.correspondence_loss = correspondence_loss
        self.activation = activation
        self.learning_rate = learning_rate
        self.iteration = 0

        if restore_folder:
            self._restore(restore_folder)
            return

        self.xb1 = tf.compat.v1.placeholder(tf.float32, shape=[None, self.dim_b1], name='xb1')
        self.xb2 = tf.compat.v1.placeholder(tf.float32, shape=[None, self.dim_b2], name='xb2')

        self.lr = tf.compat.v1.placeholder(tf.float32, shape=[], name='lr')
        self.is_training = tf.compat.v1.placeholder(tf.bool, shape=[], name='is_training')

        self._build()
        self.init_session(limit_gpu_fraction=limit_gpu_fraction, no_gpu=no_gpu)
        self.graph_init(self.sess)

    def init_session(self, limit_gpu_fraction=.4, no_gpu=False):
        """Initialize the session."""
        if no_gpu:
            config = tf.compat.v1.ConfigProto(device_count={'GPU': 0})
            self.sess = tf.compat.v1.Session(config=config)
        elif limit_gpu_fraction:
            gpu_options = tf.compat.v1.GPUOptions(per_process_gpu_memory_fraction=limit_gpu_fraction)
            config = tf.compat.v1.ConfigProto(gpu_options=gpu_options)
            self.sess = tf.compat.v1.Session(config=config)
        else:
            self.sess = tf.compat.v1.Session()

    def graph_init(self, sess=None):
        """Initialize graph variables."""
        if not sess: sess = self.sess

        self.saver = tf.compat.v1.train.Saver(tf.compat.v1.global_variables(), max_to_keep=1)

        sess.run(tf.compat.v1.global_variables_initializer())

        return self.saver

    def save(self, iteration=None, saver=None, sess=None, folder=None):
        """Save the model."""
        if not iteration: iteration = self.iteration
        if not saver: saver = self.saver
        if not sess: sess = self.sess
        if not folder: folder = self.save_folder

        savefile = os.path.join(folder, 'MAGAN')
        saver.save(sess, savefile, write_meta_graph=True)
        print("Model saved to {}".format(savefile))

    def _restore(self, restore_folder):
        """Restore the model from a saved checkpoint."""
        tf.reset_default_graph()
        self.init_session()
        ckpt = tf.train.get_checkpoint_state(restore_folder)
        self.saver = tf.train.import_meta_graph('{}.meta'.format(ckpt.model_checkpoint_path))
        self.saver.restore(self.sess, ckpt.model_checkpoint_path)
        print("Model restored from {}".format(restore_folder))

    def _build(self):
        """Construct the DiscoGAN operations."""
        self.G12 = Generator(self.dim_b2, name='G12')
        self.Gb2 = self.G12(self.xb1)
        self.Gb2 = nameop(self.Gb2, 'Gb2')

        self.G21 = Generator(self.dim_b1, name='G21')
        self.Gb1 = self.G21(self.xb2)
        self.Gb1 = nameop(self.Gb1, 'Gb1')

        self.xb2_reconstructed = self.G12(self.Gb1, reuse=True)
        self.xb1_reconstructed = self.G21(self.Gb2, reuse=True)
        self.xb1_reconstructed = nameop(self.xb1_reconstructed, 'xb1_reconstructed')
        self.xb2_reconstructed = nameop(self.xb2_reconstructed, 'xb2_reconstructed')

        self.D1 = Discriminator(name='D1')
        self.D2 = Discriminator(name='D2')

        self.D1_probs_z = self.D1(self.xb1)
        self.D1_probs_G = self.D1(self.Gb1, reuse=True)

        self.D2_probs_z = self.D2(self.xb2)
        self.D2_probs_G = self.D2(self.Gb2, reuse=True)

        self._build_loss()

        self._build_optimization()

    def _build_loss(self):
        """Collect both of the losses."""
        self._build_loss_D()
        self._build_loss_G()
        self.loss_D = nameop(self.loss_D, 'loss_D')
        self.loss_G = nameop(self.loss_G, 'loss_G')
        #self.loss_corr = nameop(self.loss_corr, "loss_corr")
        tf.compat.v1.add_to_collection('losses', self.loss_D)
        tf.compat.v1.add_to_collection('losses', self.loss_G)
        #tf.compat.v1.add_to_collection('losses', self.loss_corr)

    def _build_loss_D(self):
        """Discriminator loss."""
        losses = []
        # the true examples
        losses.append(tf.reduce_mean(tf.nn.sigmoid_cross_entropy_with_logits(logits=self.D1_probs_z, labels=tf.ones_like(self.D1_probs_z))))
        losses.append(tf.reduce_mean(tf.nn.sigmoid_cross_entropy_with_logits(logits=self.D2_probs_z, labels=tf.ones_like(self.D2_probs_z))))

        # the generated examples
        losses.append(tf.reduce_mean(tf.nn.sigmoid_cross_entropy_with_logits(logits=self.D1_probs_G, labels=tf.zeros_like(self.D1_probs_G))))
        losses.append(tf.reduce_mean(tf.nn.sigmoid_cross_entropy_with_logits(logits=self.D2_probs_G, labels=tf.zeros_like(self.D2_probs_G))))

        self.loss_D = tf.reduce_mean(losses)

    def _build_loss_G(self):
        """Generator loss."""
        losses = []
        # fool the discriminator losses
        losses.append(tf.reduce_mean(tf.nn.sigmoid_cross_entropy_with_logits(logits=self.D1_probs_G, labels=tf.ones_like(self.D1_probs_G))))
        losses.append(tf.reduce_mean(tf.nn.sigmoid_cross_entropy_with_logits(logits=self.D2_probs_G, labels=tf.ones_like(self.D2_probs_G))))

        # reconstruction losses
        losses.append(tf.math.reduce_mean((self.xb1 - self.xb1_reconstructed)**2))
        losses.append(tf.math.reduce_mean((self.xb2 - self.xb2_reconstructed)**2))

        # correspondences losses
        losses.append(1 * tf.math.reduce_mean(self.correspondence_loss(self.xb1, self.Gb2)))
        losses.append(1 * tf.math.reduce_mean(self.correspondence_loss(self.xb2, self.Gb1)))
        #self.loss_corr = 1 * tf.math.reduce_mean(self.correspondence_loss(self.xb1, self.Gb2)) + 1 * tf.math.reduce_mean(self.correspondence_loss(self.xb2, self.Gb1))

        self.loss_G = tf.math.reduce_mean(losses)

    def _build_optimization(self):
        """Build optimization components."""
        Gvars = [tv for tv in tf.compat.v1.global_variables() if 'G12' in tv.name or 'G21' in tv.name]
        Dvars = [tv for tv in tf.compat.v1.global_variables() if 'D1' in tv.name or 'D2' in tv.name]

        update_ops = tf.compat.v1.get_collection(tf.compat.v1.GraphKeys.UPDATE_OPS)
        G_update_ops = [op for op in update_ops if 'G12' in op.name or 'G21' in op.name]
        D_update_ops = [op for op in update_ops if 'D1' in op.name or 'D2' in op.name]

        with tf.control_dependencies(G_update_ops):
            optG = tf.compat.v1.train.AdamOptimizer(self.lr*10, beta1=.5, beta2=.99)
            self.train_op_G = optG.minimize(self.loss_G, var_list=Gvars, name='train_op_G')

        with tf.control_dependencies(D_update_ops):
            optD = tf.compat.v1.train.AdamOptimizer(self.lr*.0001, beta1=.5, beta2=.99)
            self.train_op_D = optD.minimize(self.loss_D, var_list=Dvars, name='train_op_D')

    def train(self, xb1, xb2):
        """Take a training step with batches from each domain."""
        self.iteration += 1

        feed = {tbn('xb1:0'): xb1,
                tbn('xb2:0'): xb2,
                tbn('lr:0'): self.learning_rate,
                tbn('is_training:0'): True}

        _ = self.sess.run([obn('train_op_G')], feed_dict=feed)
        _ = self.sess.run([obn('train_op_D')], feed_dict=feed)

    def get_layer(self, xb1, xb2, name):
        """Get a layer of the network by name for the entire datasets given in xb1 and xb2."""
        tensor_name = "{}:0".format(name)
        tensor = tbn(tensor_name)

        feed = {tbn('xb1:0'): xb1,
                tbn('xb2:0'): xb2,
                tbn('is_training:0'): False}

        layer = self.sess.run(tensor, feed_dict=feed)

        return layer

    def get_loss_names(self):
        """Return a string for the names of the loss values."""
        losses = [tns.name[:-2].replace('loss_', '').split('/')[-1] for tns in tf.compat.v1.get_collection('losses')]
        return "Losses: {}".format(' '.join(losses))

    def get_loss(self, xb1, xb2):
        """Return all of the loss values for the given input."""
        feed = {tbn('xb1:0'): xb1,
                tbn('xb2:0'): xb2,
                tbn('is_training:0'): False}

        ls = [tns for tns in tf.compat.v1.get_collection ('losses')]
        losses = self.sess.run(ls, feed_dict=feed)

        lstring = ' '.join(['{:.2E}'.format(loss) for loss in losses])
        return lstring


class Generator(object):
    """MAGAN's generator."""

    def __init__(self,
        output_dim,
        name='',
        activation=tf.nn.relu):
        """"Initialize the generator."""
        self.output_dim = output_dim
        self.activation = activation
        self.name = name

    def __call__(self, x, reuse=False):
        """Perform the feedforward for the generator."""
        #with tf.variable_scope(self.name):
        h1 = tf.keras.layers.Dense( 200, activation=self.activation,  name=self.name+'_h1')(x)
        h2 = tf.keras.layers.Dense( 100, activation=self.activation, name=self.name+'_h2')(h1)
        h3 = tf.keras.layers.Dense(50, activation=self.activation, name=self.name+'_h3')(h2)

        out = tf.keras.layers.Dense(self.output_dim, activation=None, name=self.name+'_out')(h3)

        return out

class Discriminator(object):
    """MAGAN's discriminator."""

    def __init__(self,
        name='',
        activation=tf.nn.relu):
        """Initialize the discriminator."""
        self.activation = activation
        self.name = name

    def __call__(self, x, reuse=False):
        """Perform the feedforward for the discriminator."""
        #with tf.variable_scope(self.name):
        h1 = tf.keras.layers.Dense(800, activation=self.activation,  name=self.name+'_h1')(x)
        h2 = tf.keras.layers.Dense( 400, activation=self.activation,  name=self.name+'_h2')(h1)
        h3 = tf.keras.layers.Dense( 200, activation=self.activation, name=self.name+'_h3')(h2)
        h4 = tf.keras.layers.Dense( 100, activation=self.activation,  name=self.name+'_h4')(h3)
        h5 = tf.keras.layers.Dense(50, activation=self.activation,  name=self.name+'_h5')(h4)

        out = tf.keras.layers.Dense( 1, activation=None, name=self.name+'_out')(h5)

        return out


































