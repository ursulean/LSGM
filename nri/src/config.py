_default_config = dict(

    globals=dict(
        seed=42,
        prior=None,
        add_const=False,
        eps=1e-16
    ),

    training=dict(
        gpu_id=None,
        use_early_stopping=True,
        early_stopping_metric='val_mse_loss',
        early_stopping_patience=50,
        epochs=500,
        batch_size=128,
        load_path=None,
        grad_clip_value=None,
        optimizer=dict(
            type='adam',
            learning_rate=0.0005,
            betas=[0.9, 0.999]
        ),

        scheduler=dict(
            stepsize=200,
            gamma=0.5,
        )
    ),

    data=dict(
        timesteps=49,
        name='springs',
        path='/nfs/students/summer-term-2019/project-4/datasets',
        random=dict(
            atoms=4,
            dims=100,
            examples=100,
            timesteps=200
        ),
        springs=dict(
            suffix='_springs5',
            atoms=5,
            dims=4
        ),
        weather=dict(
            atoms=5,
            dims=1,
            examples=100000,
            timesteps=100,
            suffix="",
            force_new=False,
            discard=False,
            splits=[80, 10, 10]  # TODO Make configurable
        )
    ),

    loss=dict(
        beta=1.0
    ),

    model=dict(
        prediction_steps=10,
        factor_graph=True,
        skip_first=False,
        hard=False,
        dynamic_graph=False,
        temp=0.5,
        burn_in=False,
        n_edge_types=2,
        encoder=dict(
            model='mlp',  # or CNN
            hidden_dim=256,
            dropout=0.5
        ),
        decoder=dict(
            model='rnn',  # or MLP
            hidden_dim=256,
            dropout=0.0,
            prediction_variance=5e-5
        )),

    logging=dict(
        log_step=10,
        log_dir='./logs',
        logger_config="",  # str
        store_models=True
    )
)
