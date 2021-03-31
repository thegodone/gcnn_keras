import time

import matplotlib as mpl
import numpy as np
import tensorflow as tf

mpl.use('Agg')
import matplotlib.pyplot as plt
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler

from kgcnn.data.qm.qm9 import qm9_graph
from kgcnn.literature.NMPN import getmodelNMPN
from kgcnn.utils.learning import lr_lin_reduction
from kgcnn.utils.data import ragged_tensor_from_nested_numpy
from kgcnn.utils.loss import ScaledMeanAbsoluteError

# Download and generate dataset.
# QM9 has about 200 MB of data
# You need at least 10 GB of RAM to load and process full dataset into memory.
labels, nodes, edges, edge_indices, graph_state = qm9_graph(max_mols=10000)  # max is 133885

# Select LUMO as target and convert into eV from H
# Standardize output with scikit-learn std-scaler
labels = labels[:, 7:8] * 27.2114
scaler = StandardScaler()
labels_std = scaler.fit_transform(labels)
data_unit = 'eV'

# Train Test split
labels_train, labels_test, nodes_train, nodes_test, edges_train, edges_test, edge_indices_train, edge_indices_test, graph_state_train, graph_state_test = train_test_split(
    labels, nodes, edges, edge_indices, graph_state, test_size=0.15, random_state=42)
del labels, nodes, edges, edge_indices, graph_state  # Free memory after split, if possible

# Convert to tf.RaggedTensor or tf.tensor
# A copy of the data is generated by ragged_tensor_from_nested_numpy()
nodes_train, edges_train, edge_indices_train, graph_state_train = ragged_tensor_from_nested_numpy(
    nodes_train), ragged_tensor_from_nested_numpy(edges_train), ragged_tensor_from_nested_numpy(
    edge_indices_train), tf.constant(graph_state_train)

nodes_test, edges_test, edge_indices_test, graph_state_test = ragged_tensor_from_nested_numpy(
    nodes_test), ragged_tensor_from_nested_numpy(edges_test), ragged_tensor_from_nested_numpy(
    edge_indices_test), tf.constant(graph_state_test)

# Define input and output data
xtrain = nodes_train, edges_train, edge_indices_train, graph_state_train
xtest = nodes_test, edges_test, edge_indices_test, graph_state_test
ytrain = labels_train
ytest = labels_test

model = getmodelNMPN(
    # Input
    input_node_shape=[None],
    input_edge_shape=[None, 20],
    input_state_shape=[1],
    input_node_vocab=10,
    input_node_embedd=32,
    input_type='ragged',
    # Output
    output_embedd='graph',
    output_use_bias=[True, True, False],
    output_dim=[25, 10, 1],
    output_activation=['selu', 'selu', 'linear'],
    output_type='padded',
    # Model specific
    depth=3,
    node_dim=64,
    use_set2set=True,
    set2set_dim=32,
    use_bias=True,
    activation='selu',
    is_sorted=True,
    has_unconnected=False
)

# Define learning rate and epochs
learning_rate_start = 0.5e-3
learning_rate_stop = 1e-5
epo = 500
epomin = 400
epostep = 10

# Compile model with optimizer and learning rate
# The scaled metric is meant to display the inverse-scaled mae values (optional)
optimizer = tf.keras.optimizers.Adam(lr=learning_rate_start)
mae_metric = ScaledMeanAbsoluteError((1, 1))
mae_metric.set_scale(np.expand_dims(scaler.scale_, axis=0))
cbks = tf.keras.callbacks.LearningRateScheduler(lr_lin_reduction(learning_rate_start, learning_rate_stop, epomin, epo))
model.compile(loss='mean_squared_error',
              optimizer=optimizer,
              metrics=[mae_metric])
print(model.summary())

# Start training
start = time.process_time()
hist = model.fit(xtrain, ytrain,
                 epochs=epo,
                 batch_size=64,
                 callbacks=[cbks],
                 validation_freq=epostep,
                 validation_data=(xtest, ytest),
                 verbose=2
                 )
stop = time.process_time()
print("Print Time for taining: ", stop - start)

trainlossall = np.array(hist.history['mean_absolute_error'])
testlossall = np.array(hist.history['val_mean_absolute_error'])

# Predict lumo with model
pred_test = scaler.inverse_transform(model.predict(xtest))
true_test = scaler.inverse_transform(ytest)
mae_valid = np.mean(np.abs(pred_test - true_test))

# Plot loss vs epochs
plt.figure()
plt.plot(np.arange(trainlossall.shape[0]), trainlossall, label='Training Loss', c='blue')
plt.plot(np.arange(epostep, epo + epostep, epostep), testlossall, label='Test Loss', c='red')
plt.scatter([trainlossall.shape[0]], [mae_valid], label="{0:0.4f} ".format(mae_valid) + "[" + data_unit + "]", c='red')
plt.xlabel('Epochs')
plt.ylabel('Loss ' + "[" + data_unit + "]")
plt.title('Message passing Loss')
plt.legend(loc='upper right', fontsize='x-large')
plt.savefig('nmpn_loss.png')
plt.show()

# Predicted vs Actual
plt.figure()
plt.scatter(pred_test, true_test, alpha=0.3, label="MAE: {0:0.4f} ".format(mae_valid) + "[" + data_unit + "]")
plt.plot(np.arange(np.amin(true_test), np.amax(true_test), 0.05),
         np.arange(np.amin(true_test), np.amax(true_test), 0.05), color='red')
plt.xlabel('Predicted')
plt.ylabel('Actual')
plt.legend(loc='upper left', fontsize='x-large')
plt.savefig('nmpn_predict.png')
plt.show()
